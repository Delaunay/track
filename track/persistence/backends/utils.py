# -*- coding: utf-8 -*-
"""
:mod:`orion.core.io.database` -- Wrappers for database frameworks
=================================================================

.. module:: database
   :platform: Unix
   :synopsis: Import name for wrappers of database frameworks.

Contains :class:`AbstractDB`, an interface for databases.
Currently, implemented wrappers:

   - :class:`orion.core.io.database.mongodb.MongoDB`

"""
from abc import abstractmethod, abstractproperty
import copy
import logging

from track.persistence.utils import parse_uri


# pylint: disable=too-many-public-methods
class AbstractDB:
    """Base class for database framework wrappers.

    Attributes
    ----------
    host : str
       It can be either:
          1. Known hostname or IP address in which database server resides.
          2. URI: A database framework specific connection string.
    name : str
       Name of database containing experiments.
    port : int
       Port that database server listens to for requests.
    username : str
        Name of user with write/read permissions to database with name `name`.
    password : str
        Secret phrase of user, `username`.

    """

    ASCENDING = 0
    DESCENDING = 1

    def __init__(self, uri):
        """Init method, see attributes of :class:`AbstractDB`."""

        # host = 'localhost', name = None,
        # port = None, username = None, password = None, ** kwargs
        self.resource = uri
        args = parse_uri(uri)

        self.host = args.get('address')
        self.name = args.get('query', {}).pop('name', 'default')
        self.port = args.get('port')
        self.username = args.get('username')
        self.password = args.get('password')
        self.options = args.get('query')

        self._db = None
        self._conn = None
        self.initiate_connection()

    @abstractproperty
    def is_connected(self):
        """True, if practical connection has been achieved."""
        pass

    @abstractmethod
    def initiate_connection(self):
        """Connect to database, unless `AbstractDB` `is_connected`.

        Raises
        ------
        DatabaseError
            If connection or authentication fails

        """
        pass

    @abstractmethod
    def close_connection(self):
        """Disconnect from database, if `AbstractDB` `is_connected`."""
        pass

    @abstractmethod
    def ensure_index(self, collection_name, keys, unique=False):
        """Create given indexes if they do not already exist in database.

        Parameters
        ----------
        collection_name : str
           A collection inside database, a table.
        keys: str or list of tuples
           Can be a string representing a key to index, or a list of tuples
           with the structure `[(key_name, sort_order)]`. `key_name` must be a
           string and sort_order can be either `AbstractDB.ASCENDING` or
           AbstractDB.DESCENDING`.
        unique: bool, optional
           Ensure each document have a different key value. If not, operations
           like `write()` and `read_and_write()` will raise
           `DuplicateKeyError`.
           Defaults to False.

        Notes
        -----
        Depending on the backend, the indexing operation might operate in
        background. This means some operations on the database might occur
        before the indexes are totally built.

        """
        pass

    @abstractmethod
    def index_information(self, collection_name):
        """Return dict of names and sorting order of indexes

        Paramaters
        ----------
        collection_name : str
           A collection inside database, a table.

        Returns
        -------
        dict
            Dictionary of indexes where each key is the name in the format {name}_{order}
            and each value represents whether the index is unique.

        """
        return self._db[collection_name].index_information()

    @abstractmethod
    def drop_index(self, collection_name, name):
        """Remove index from the database

        Paramaters
        ----------
        collection_name : str
           A collection inside database, a table.
        name: str
            Index name in the format {name}_{order}

        """
        self._db[collection_name].drop_index(name)

    @abstractmethod
    def write(self, collection_name, data, query=None):
        """Write new information to a collection. Perform insert or update.

        Parameters
        ----------
        collection_name : str
           A collection inside database, a table.
        data : dict or list of dicts
           New data that will **be inserted** or that will **update** entries.
        query : dict, optional
           Assumes an update operation: filter entries in collection to be updated.

        Returns
        -------
        int
            Number of new documents if no query, otherwise number of modified documents.

        Notes
        -----
        In the case of an insert operation, `data` variable will be updated
        to contain a unique *_id* key.

        In the case of an update operation, if `query` fails to find a
        document that matches, no operation is performed.

        Raises
        ------
        DuplicateKeyError
            If the operation is creating duplicate keys in two different documents. Only occurs if
            the keys have unique indexes. See :meth:`AbstractDB.ensure_index` for more information
            about indexes.

        """
        pass

    @abstractmethod
    def read(self, collection_name, query=None, selection=None):
        """Read a collection and return a value according to the query.

        Parameters
        ----------
        collection_name : str
           A collection inside database, a table.
        query : dict, optional
           Filter entries in collection.
        selection : dict, optional
           Elements of matched entries to return, the projection.

        Returns
        -------
        list
            List of matched document[s]

        """
        pass

    @abstractmethod
    def read_and_write(self, collection_name, query, data, selection=None):
        """Read a collection's document and update the found document.

        If many documents are found, the first one is selected.

        Returns the updated document, or None if nothing found.

        Parameters
        ----------
        collection_name : str
           A collection inside database, a table.
        query : dict
           Filter entries in collection.
        data : dict or list of dicts
           New data that will **update** the entry.
        selection : dict, optional
           Elements of matched entries to return, the projection.

        Returns
        -------
        dict or None
            Updated first matched document or None if nothing found

        Raises
        ------
        DuplicateKeyError
            If the operation is creating duplicate keys in two different documents. Only occurs if
            the keys have unique indexes. See :meth:`AbstractDB.ensure_index` for more information
            about indexes.

        """
        pass

    @abstractmethod
    def count(self, collection_name, query=None):
        """Count the number of documents in a collection which match the `query`.

        Parameters
        ----------
        collection_name : str
           A collection inside database, a table.
        query : dict
           Filter entries in collection.

        """
        pass

    @abstractmethod
    def remove(self, collection_name, query):
        """Delete from a collection document[s] which match the `query`.

        Parameters
        ----------
        collection_name : str
           A collection inside database, a table.
        query : dict
           Filter entries in collection.

        Returns
        -------
        int
            Number of documents removed

        """
        pass


# pylint: disable=too-few-public-methods
class ReadOnlyDB:
    """Read-only view on a database."""

    __slots__ = ('_database', )

    #                     Attributes
    valid_attributes = (["host", "name", "port", "username", "password"] +
                        # Properties
                        ["is_connected"] +
                        # Methods
                        ["initiate_connection", "close_connection", "read", "count"])

    def __init__(self, database):
        """Init method, see attributes of :class:`AbstractDB`."""
        self._database = database

    def __getattr__(self, attr):
        """Get attribute only if valid"""
        if attr not in self.valid_attributes:
            raise AttributeError("Cannot access attribute %s on view-only experiments." % attr)

        return getattr(self._database, attr)


class DatabaseError(RuntimeError):
    """Exception type used to delegate responsibility from any database
    implementation's own Exception types.
    """

    pass


class DuplicateKeyError(DatabaseError):
    """Exception type used when a write attempt is made but the new document
    have an index already contained in the database.
    """

    pass


class OutdatedDatabaseError(DatabaseError):
    """Exception type used when the database is outdated."""

    pass


# set per-module log level
logging.getLogger('filelock').setLevel('ERROR')


def flatten(dictionary):
    """Turn all nested dict keys into a {key}.{subkey} format"""
    def _flatten(dictionary):
        if dictionary == {}:
            return dictionary

        key, value = dictionary.popitem()
        if not isinstance(value, dict) or not value:
            new_dictionary = {key: value}
            new_dictionary.update(_flatten(dictionary))
            return new_dictionary

        flat_sub_dictionary = _flatten(value)
        for flat_sub_key in list(flat_sub_dictionary.keys()):
            flat_key = key + '.' + flat_sub_key
            flat_sub_dictionary[flat_key] = flat_sub_dictionary.pop(flat_sub_key)

        new_dictionary = flat_sub_dictionary
        new_dictionary.update(_flatten(dictionary))
        return new_dictionary

    return _flatten(copy.deepcopy(dictionary))


def unflatten(dictionary):
    """Turn all keys with format {key}.{subkey} into nested dictionaries"""
    unflattened_dictionary = dict()
    for key, value in dictionary.items():
        parts = key.split(".")
        sub_dictionary = unflattened_dictionary
        for part in parts[:-1]:
            if part not in sub_dictionary:
                sub_dictionary[part] = dict()
            sub_dictionary = sub_dictionary[part]
        sub_dictionary[parts[-1]] = value
    return unflattened_dictionary