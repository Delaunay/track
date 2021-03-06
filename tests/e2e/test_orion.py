import orion.core.cli
from tests.config import is_travis, remove
import sys
from multiprocessing import Process
import pytest
import subprocess
import os
import shutil
import time

try:
    from pytest_cov.embed import cleanup_on_sigterm
except ImportError:
    pass
else:
    cleanup_on_sigterm()


@pytest.mark.skipif(is_travis(), reason='Travis is too slow')
def test_orion_poc(backend='track:file://orion_results.json?objective=epoch_loss', max_trials=2):
    remove('orion_results.json')

    os.environ['ORION_STORAGE'] = backend
    _, uri = os.environ.get('ORION_STORAGE').split(':', maxsplit=1)

    cwd = os.getcwd()
    os.chdir(os.path.dirname(__file__))

    multiple_of_8 = [8 * i for i in range(32 // 8, 512 // 8)]

    orion.core.cli.main([
        '-vv', '--debug', 'hunt',
        '--config', 'orion.yaml', '-n', 'random', #'--metric', 'error_rate',
        '--max-trials', str(max_trials),
        './end_to_end.py', f'--batch-size~choices({multiple_of_8})', '--backend', uri
    ])

    os.chdir(cwd)
    remove('orion_results.json')


@pytest.mark.skipif(is_travis(), reason='Travis is too slow')
def test_orion_cockroach():
    from track.distributed.cockroachdb import CockRoachDB

    db = CockRoachDB(location='/tmp/cockroach', addrs='localhost:8123')
    db.start(wait=True)

    try:
        test_orion_poc(
            backend='track:cockroach://localhost:8123?objective=epoch_loss',
            max_trials=2
        )
    except Exception as e:
        raise e

    finally:
        db.stop()


def mongodb():

    with subprocess.Popen('mongod --dbpath /tmp/mongodb', stdout=subprocess.DEVNULL, shell=True) as proc:
        while True:
            if proc.poll() is not None:
                break
            else:
                proc.stdout.readline()
                time.sleep(0.01)

        shutil.rmtree('/tmp/mongodb')


if __name__ == '__main__':
    sys.stderr = sys.stdout

    # test_orion_poc(backend='track:file://orion_results.json?objective=epoch_loss')
    test_orion_cockroach()

