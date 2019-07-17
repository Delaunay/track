import time
import logging
from filelock import FileLock, logger as file_lock_logger
from typing import Callable

from track.configuration import options
from track.utils.signal import SignalHandler
from track.utils.log import error, warning, debug
from track.utils.debug import print_stack
from track.structure import Project, Trial, TrialGroup
from track.persistence.protocol import Protocol
from track.persistence.storage import load_database, LocalStorage
from track.persistence.utils import parse_uri
from track.containers.types import float32
from track.aggregators.aggregator import Aggregator
from track.aggregators.aggregator import RingAggregator
from track.aggregators.aggregator import StatAggregator
from track.aggregators.aggregator import ValueAggregator
from track.aggregators.aggregator import TimeSeriesAggregator


value_aggregator = ValueAggregator.lazy()
ring_aggregator = RingAggregator.lazy(10, float32)
stat_aggregator = StatAggregator.lazy(1)
ts_aggregator = TimeSeriesAggregator.lazy()

file_lock_logger().setLevel(logging.ERROR)


def _make_container(step, aggregator):
    if step is None:
        if aggregator is None:
            # favor ts aggregator because it has an option to cut the TS for printing purposes
            return ts_aggregator()
        return aggregator()
    else:
        return dict()


class _NoLockLock:
    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            raise exc_type


def make_lock(name, eager):
    if eager:
        return FileLock(name, timeout=options('log.backend.lock_timeout', 5))
    return _NoLockLock()


class ConcurrentWrite(Exception):
    pass


_updating_references = False


class _UpdatingRefs:
    def __enter__(self):
        global _updating_references
        _updating_references = True

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _updating_references
        _updating_references = False


def update_references(self: 'FileProtocol', args, kwargs, atomic=False):
    """Iterate through the arguments and replace stale objects by their new handle.
    In case `atomic` is specified. we check that the old object and new object were not modified;
    i.e we check that both db_version tags match
    """
    global _updating_references

    updated_args = []
    updated_kwargs = {}

    def select(a, b):
        if a is not None:
            if isinstance(a, list):
                a = a[0]

            if not atomic:
                return a
            elif a.metadata.get('_update_count', 0) == b.metadata.get('_update_count', 0):
                return a
            else:
                old = a.metadata.get('_update_count', 0)
                new = b.metadata.get('_update_count', 0)

                raise ConcurrentWrite(f'Concurrent write detected {old} != {new}')
        else:
            return b

    def update(arg):
        if _updating_references:
            return arg

        if isinstance(arg, Trial):
            with _UpdatingRefs():
                t = self.get_trial(arg)
            return select(t, arg)

        elif isinstance(arg, TrialGroup):
            with _UpdatingRefs():
                g = self.get_trial_group(arg)
            return select(g, arg)

        elif isinstance(arg, Project):
            with _UpdatingRefs():
                p = self.get_project(arg)
            return select(p, arg)
        else:
            return arg

    # we need to update the objects
    for arg in args:
        updated_args.append(update(arg))

    for name, arg in kwargs.items():
        updated_kwargs[name] = update(arg)

    return args, kwargs


def lock_guard(readonly, atomic=False):
    """Protect a function call with a lock. reload the database before the action and save it afterwards"""

    def lock_guard_decorator(fun):

        def _lock_guard(self, *args, **kwargs):
            with self.lock:
                if self.eager:
                    self.storage: LocalStorage = load_database(self.path)

                # use heavily use obj references so when we reload the db
                # we need to make sure those objects are updated
                args, kwargs = update_references(self, args, kwargs, atomic)

                val = fun(self, *args, **kwargs)

                if self.eager and not readonly:
                    self.commit()

            return val

        return _lock_guard

    return lock_guard_decorator


lock_write = lock_guard(readonly=False)
lock_atomic_write = lock_guard(readonly=False, atomic=True)
lock_read = lock_guard(readonly=True)


class LockFileRemover(SignalHandler):
    def __init__(self, filename):
        super(LockFileRemover, self).__init__()
        self.file_name = filename

    def remove(self):
        import os
        if os.path.exists(self.file_name):
            os.remove(self.file_name)

    def sigterm(self, signum, frame):
        self.remove()

    def sigint(self, signum, frame):
        self.remove()

    def atexit(self):
        self.remove()


class FileProtocol(Protocol):
    """Local File storage to manage experiments

    Parameters
    ----------

    uri: str
        resource to use to store the experiment `file://my_file.json`

    strict: bool
        forces the storage to be correct.
        if we use the file protocol as an in-memory storage we might get some inconsistencies
        we can use this flag to ignore them

    eager: bool
        eagerly update the underlying files. This is necessary if multiple processes are reading from the file

    """

    def __init__(self, uri, strict=True, eager=True):
        uri = parse_uri(uri)

        # file:test.json
        path = uri.get('path')

        if not path:
            # file://test.json
            path = uri.get('address')

        self.path = path
        self.storage: LocalStorage = load_database(path)
        self.chronos = {}
        self.strict = strict
        self.eager = eager
        self.lock = make_lock(f'{path}.lock', eager)
        self.signal_handler = LockFileRemover(f'{path}.lock')

    def _inc_trial(self, trial):
        # print_stack()
        trial.metadata['_update_count'] = trial.metadata.get('_update_count', 0) + 1

    @lock_write
    def log_trial_start(self, trial):
        acc = ValueAggregator()
        trial.chronos['runtime'] = acc
        self.chronos['runtime'] = time.time()
        self._inc_trial(trial)

    @lock_write
    def log_trial_finish(self, trial, exc_type, exc_val, exc_tb):
        start_time = self.chronos['runtime']
        acc = trial.chronos['runtime']
        acc.append(time.time() - start_time)
        self._inc_trial(trial)

    @lock_write
    def log_trial_metadata(self, trial: Trial, aggregator: Callable[[], Aggregator] = value_aggregator, **kwargs):
        for k, v in kwargs.items():
            container = trial.metadata.get(k)

            if container is None:
                container = _make_container(None, aggregator)
                trial.metadata[k] = container

            container.append(v)
        self._inc_trial(trial)

    @lock_write
    def log_trial_chrono_start(self, trial, name: str, aggregator: Callable[[], Aggregator] = StatAggregator.lazy(1),
                               start_callback=None,
                               end_callback=None):
        agg = trial.chronos.get(name)
        if agg is None:
            agg = aggregator()
            trial.chronos[name] = agg

        self.chronos[name] = time.time()
        self._inc_trial(trial)

    @lock_write
    def log_trial_chrono_finish(self, trial, name, exc_type, exc_val, exc_tb):
        start_time = self.chronos[name]
        acc = trial.chronos[name]
        acc.append(time.time() - start_time)
        self._inc_trial(trial)

    @lock_write
    def log_trial_metrics(self, trial: Trial, step: any = None, aggregator: Callable[[], Aggregator] = None, **kwargs):
        for k, v in kwargs.items():
            container = trial.metrics.get(k)

            if container is None:
                container = _make_container(step, aggregator)
                trial.metrics[k] = container

            if step is not None and isinstance(container, dict):
                container[step] = v
            elif step:
                container.append((step, v))
            else:
                container.append(v)
        self._inc_trial(trial)

    @lock_write
    def add_trial_tags(self, trial, **kwargs):
        trial.tags.update(kwargs)
        self._inc_trial(trial)

    @lock_write
    def log_trial_arguments(self, trial, **kwargs):
        trial.parameters.update(kwargs)
        self._inc_trial(trial)

    @lock_atomic_write
    def set_trial_status(self, trial, status, error=None):
        trial.status = status
        if error is not None:
            trial.errors.append(error)
        self._inc_trial(trial)

    # Object Creation
    @lock_read
    def get_project(self, project: Project):
        debug(f'look for (project: {project.name})')
        return self.storage.objects.get(project.uid)

    @lock_write
    def new_project(self, project: Project):
        debug(f'create new (project: {project.name})')

        if project.uid in self.storage.objects:
            error(f'Cannot insert project; (uid: {project.uid}) already exists!')
            return self.get_project(project)

        self.storage.objects[project.uid] = project
        self.storage.project_names[project.name] = project.uid
        self.storage.projects.add(project.uid)

        return project

    @lock_read
    def get_trial_group(self, group: TrialGroup):
        return self.storage.objects.get(group.uid)

    @lock_write
    def new_trial_group(self, group: TrialGroup):
        if group.uid in self.storage.objects:
            error(f'Cannot insert group; (uid: {group.uid}) already exists!')
            return

        project = self.storage.objects.get(group.project_id)
        if self.strict:
            assert project is not None, 'Cannot create a group without an associated project'
            project.groups.append(group)

        self.storage.objects[group.uid] = group
        self.storage.groups.add(group.uid)
        self.storage.group_names[group.name] = group.uid
        return group

    @lock_read
    def get_trial(self, trial: Trial):
        trials = []

        if trial.uid in self.storage.objects:
            trial_hash = trial.hash

            for k, obj in self.storage.objects.items():
                if k.startswith(trial_hash):
                    trials.append(obj)

            return trials
        return None

    @lock_write
    def new_trial(self, trial: Trial):
        if trial.uid in self.storage.objects:
            trials = self.get_trial(trial)

            max_rev = 0
            for t in trials:
                max_rev = max(max_rev, t.revision)

            warning(f'Trial was already completed. Increasing revision number (rev={max_rev + 1})')
            trial.revision = max_rev + 1
            trial._hash = None

        self.storage.objects[trial.uid] = trial
        self.storage.trials.add(trial.uid)

        if trial.project_id is not None:
            project = self.storage.objects.get(trial.project_id)

            if project is not None or self.strict:
                project.trials.append(trial)
        else:
            warning('Orphan trial')

        if trial.group_id is not None:
            group = self.storage.objects.get(trial.group_id)
            if group is not None or self.strict:
                group.trials.append(trial.uid)

        return trial

    @lock_write
    def add_project_trial(self, project, trial):
        trial.project_id = project.uid
        project.trials.append(trial)

    @lock_write
    def add_group_trial(self, group, trial):
        if group is None and not self.strict:
            return

        trial.group_id = group.uid
        group.trials.append(trial.uid)

    def commit(self, file_name_override=None, **kwargs):
        with self.lock:
            self.storage.commit(file_name_override=file_name_override, **kwargs)

    @lock_read
    def _fetch_objects(self, objects, query, strict=False):
        matching_objects = []

        for obj_id in objects:
            obj = self.storage.objects.get(obj_id)

            if obj is None:
                err = f'stale trial (id: {obj_id}) something is wrong'
                if strict:
                    raise RuntimeError(err)
                else:
                    warning(err)
                continue

            is_selected = execute_query(obj, query)

            if is_selected:
                matching_objects.append(obj)

        return matching_objects

    @lock_read
    def fetch_trials(self, query):
        return self._fetch_objects(self.storage.trials, query)

    @lock_read
    def fetch_groups(self, query):
        return self._fetch_objects(self.storage.groups, query)

    @lock_read
    def fetch_projects(self, query):
        return self._fetch_objects(self.storage.projects, query)


def execute_query(obj, query):
    """ check if the object `obj` matches the query.
        The query is a dictionary specifying constraint on each of the object attributes

        {
            attr1: value            # attr1 should be equal to value
            attr2: {                # attr2 should be inside the list of values
                '$in': [1, 2, 3]
            }
        }
    """
    is_selected = True
    # a query can be a dict of a list of conditions
    # allowing a list enable users to make sure the conditions are executed in a specific order
    # this can be used to speed up query. You can put the most strict condition first to reduce the number of checks
    # we have to do to select a query
    items = None
    if isinstance(query, dict):
        items = query.items()
    else:
        items = list(query)

    for attr, condition in items:
        if not hasattr(obj, attr):
            warning(f'(obj: {type(obj)}) has no (attribute: {attr})')
            continue

        # This is a complex query that needs to be further processed
        if isinstance(condition, dict):
            if len(condition) == 1:
                fun_name, args = list(condition.items())[0]

                fun = _query_fun.get('$in')
                if fun is None:
                    raise RuntimeError(f'(function: {fun_name}) is not understood')

                is_selected &= fun(obj, attr, args)

            else:
                raise RuntimeError(f'(query:  {query}) was not understood')

        # this is a simple value
        else:
            is_selected &= getattr(obj, attr) == condition

        # shortcut
        if not is_selected:
            return False

    return is_selected


def query_in(obj, attr, choices):
    return getattr(obj, attr) in choices


_query_fun = {
    '$in': query_in
}


