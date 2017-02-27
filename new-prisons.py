#!/usr/bin/env python3


# new-prisons.py
#
# this script populates the local development postgres instance from the discovery registers
# it works by doing the following:
#   - download the prisons RSF
#   - iterate through each item, store in a local levelDB instance
#   - iterate through each entry, store as a record in a local levelDB instance (using primary key as key):
#   - finally, for each record key:
#     - if there's an address in the item, fetch it from the address register,
#          fetch the corresponding street, and store its name
#     - store in postgres
#   - store the number of entries we've processed in levelDB so that we don't process them again
#
# This means that each time you run this script, it will only check
# for new entries in the prison register, but it will re-check all
# address and street records in the corresponding registers.
#
# not (yet) done:
#   - check if a record exists in the pg db already before inserting (there's no unique constraint on the prison code field)


from binascii import hexlify
import dateutil.parser
import datetime
import gevent.pool
import grequests
import hashlib
import json
import leveldb
import psycopg2


POSTGRES_DB = "REGISTER_INDEX_SERVICE_DEVELOPMENT"


class RsfProcessor(object):
    def __init__(self):
        self.item_store = leveldb.LevelDB('./item_db')
        self.pgconn = psycopg2.connect("dbname="+POSTGRES_DB)
        self.pool = gevent.pool.Pool(200) # concurrent requests
        try:
            self.entries_processed = int.from_bytes(self.item_store.Get(b'entries_processed'), byteorder='big')
        except KeyError:
            self.entries_processed = 0


    def fetch_address(self, uprn):
        print('fetching address ' + uprn)
        address_req = grequests.get('https://address.discovery.openregister.org/record/%s.json' % uprn)
        address_resp = grequests.send(address_req, self.pool).get().response
        if address_resp.status_code == 404:
            print("WARNING: uprn %s resulted in 404" % uprn)
            return
        address = address_resp.json()
        print('fetching street ' + address['street'])
        street_req = grequests.get('https://street.discovery.openregister.org/record/%s.json' % address['street'])
        street = grequests.send(street_req, self.pool).get().response.json()
        address['street'] = street
        return address


    def resolve_record(self, item_hash):
        item_data = self.item_store.Get(item_hash)
        item = json.loads(item_data.decode('utf-8'))
        address = None
        if 'address' in item:
            address = self.fetch_address(item['address'])

        street_name = None
        if address:
            street_name = address['street']['name']

        end_date = None
        start_date = None
        if 'start-date' in item:
            start_date = dateutil.parser.parse(item['start-date']).date()
        if 'end-date' in item:
            end_date = dateutil.parser.parse(item['end-date']).date()
        with self.pgconn.cursor() as cursor:
            cursor.execute("INSERT INTO prisons (name, code, address, created_at, updated_at, closed, opened) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (item['name'], item['prison'], street_name, datetime.datetime.now(), datetime.datetime.now(), end_date, start_date))
            self.pgconn.commit()
        


    def resolve_records(self):
        for code, item_hash in self.item_store.RangeIter(key_from=b'prison:', key_to=b'prison:ZZZ'):
            self.pool.spawn(self.resolve_record, item_hash)
        self.pool.join()


    def assert_root_hash(self, root_hash):
        pass


    def add_item(self, item_json):
        hash = b'sha-256:' + hexlify(hashlib.sha256(item_json).digest())
        print('putting ' + str(hash))
        self.item_store.Put(hash, item_json)


    def append_entry(self, timestamp, item_hash, key):
        self.item_store.Put(b'prison:'+key, item_hash)

        self.entries_processed += 1            


    def process(self, command, args):
        if (command == b'assert-root-hash'):
            self.assert_root_hash(*args)
        elif (command == b'add-item'):
            self.add_item(*args)
        elif (command == b'append-entry'):
            self.append_entry(*args)
        else:
            raise NameError(b'unknown command: ' + command)


    def close(self):
        self.pgconn.close()
        # item_store?
        self.item_store.Put(b'entries_processed', self.entries_processed.to_bytes(8,byteorder='big'))




register_detail = grequests.map([grequests.get('https://prison.discovery.openregister.org/register.json')])[0].json()
total_entries = register_detail['total-entries']

proc = RsfProcessor()
entries = proc.entries_processed
print('starting from entry %d' % entries)
rsf_url = 'https://prison.discovery.openregister.org/download-rsf/%d/%d' % (entries, total_entries)
rsf_req = grequests.map([grequests.get(rsf_url, stream=True)])[0]
if rsf_req.status_code != 200:
    print("couldn't get RSF from url %s, got status %d" % (rsf_url, rsf_req.status_code))
    exit()

rsf_req.encoding = 'utf-8'

for line in rsf_req.iter_lines():
    [command, *args] = line.rstrip().split(b'\t')
    print(command)
    print(args)
    proc.process(command,args)

proc.resolve_records()
proc.close()
