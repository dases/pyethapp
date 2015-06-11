import os
from devp2p.service import BaseService
from gevent.event import Event
import leveldb
from ethereum import slogging
import time

slogging.set_level('db', 'debug')
log = slogging.get_logger('db')

compress = decompress = lambda x: x


"""
memleak in py-leveldb


25140 ralf      20   0 3360m 1.3g  53m S    3  4.2   4:12.71 pyethapp
26167 ralf      20   0 2943m 1.0g  44m S    1  3.3   3:19.51 pyethapp


25140 ralf      20   0 3531m 1.5g  61m S    1  4.7   5:07.49 pyethapp
26167 ralf      20   0 3115m 1.1g  47m S    1  3.6   4:03.54 pyethapp


mit reload_db()
 4096 ralf      20   0 1048m 362m  14m S    2  1.1   1:21.97 pyethapp
 4109 ralf      20   0  975m 307m  14m S    2  1.0   1:16.03 pyethapp

 4096 ralf      20   0  903m 431m 9484 S    2  1.3   1:54.29 pyethapp
 4109 ralf      20   0  807m 367m 8852 S    1  1.1   1:47.01 pyethapp

 4109 ralf      20   0 2609m 640m  60m S    3  2.0   2:41.05 pyethapp
 4096 ralf      20   0 1232m 521m  14m S    6  1.6   2:28.68 pyethapp



deserializing all blocks + pow + add to list:
1GB after 300k blocks


reading all entries == 400MB
+ check pow every 1000 32 caches = 580MB
+ check pow every 1000 1 cache = 590MB


"""

class LevelDB(object):

    def __init__(self, dbfile):
        self.uncommitted = dict()
        log.info('opening LevelDB', path=dbfile)
        self.dbfile = dbfile
        self.db = leveldb.LevelDB(dbfile)
        self.commit_counter = 0

    def reopen(self):
        del self.db
        self.db = leveldb.LevelDB(self.dbfile)

    def get(self, key):
        log.trace('getting entry', key=key.encode('hex')[:8])
        if key in self.uncommitted:
            if self.uncommitted[key] is None:
                raise KeyError("key not in db")
            log.trace('from uncommitted')
            return self.uncommitted[key]
        log.trace('from db')
        o = decompress(self.db.Get(key))
        self.uncommitted[key] = o
        return o

    def put(self, key, value):
        log.trace('putting entry', key=key.encode('hex')[:8], len=len(value))
        self.uncommitted[key] = value

    def commit(self):
        log.debug('committing', db=self)
        batch = leveldb.WriteBatch()
        for k, v in self.uncommitted.items():
            if v is None:
                batch.Delete(k)
            else:
                batch.Put(k, compress(v))
        self.db.Write(batch, sync=False)
        self.uncommitted.clear()
        log.debug('committed', db=self, num=len(self.uncommitted))
        self.commit_counter += 1
        if self.commit_counter % 100 == 0:
            self.reopen()

    def delete(self, key):
        log.trace('deleting entry', key=key)
        self.uncommitted[key] = None

    def _has_key(self, key):
        try:
            self.get(key)
            return True
        except KeyError:
            return False

    def __contains__(self, key):
        return self._has_key(key)

    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.db == other.db

    def __repr__(self):
        return '<DB at %d uncommitted=%d>' % (id(self.db), len(self.uncommitted))


class LevelDBService(LevelDB, BaseService):

    """A service providing an interface to a level db."""

    name = 'db'
    default_config = dict(data_dir='')

    def __init__(self, app):
        BaseService.__init__(self, app)
        assert self.app.config['data_dir']
        self.uncommitted = dict()
        self.stop_event = Event()
        dbfile = os.path.join(self.app.config['data_dir'], 'leveldb')
        LevelDB.__init__(self, dbfile)

    def _run(self):
        self.stop_event.wait()

    def stop(self):
        self.stop_event.set()
        # commit?
        log.debug('closing db')
