#!/usr/bin/env python3


# new-prisons.py
#
# this script populates the local development postgres instance from the discovery registers
# it works by doing the following:
#   - download the prisons RSF
#   - iterate through each item, store in a local levelDB instance
#   - iterate through each entry:
#     - if there's an address, fetch it from the address register, fetch the corresponding street, and store its name
#     - store in postgres
#
# not (yet) done:
#   - be more incremental: remember how many entries we've processed and only request from that point forward next time
#   - check if a record exists in the pg db already before inserting (there's no unique constraint on the prison code field)
#   - check for address/street register updates as well as prison register updates


from binascii import hexlify
import dateutil.parser
import fileinput
import hashlib
import json
import leveldb
import psycopg2
import requests


item_store = leveldb.LevelDB('./item_db')
# yeah, it's global state. bite me.
pgconn = psycopg2.connect("dbname=REGISTER_INDEX_SERVICE_DEVELOPMENT")


def fetch_address(uprn):
    print('fetching address ' + uprn)
    address_r = requests.get('https://address.discovery.openregister.org/record/%s.json' % uprn)
    if address_r.status_code == 404:
        print("WARNING: uprn %s resulted in 404" % uprn)
        return
    address = requests.get('https://address.discovery.openregister.org/record/%s.json' % uprn).json()
    print('fetching street ' + address['street'])
    street = requests.get('https://street.discovery.openregister.org/record/%s.json' % address['street']).json()
    address['street'] = street
    return address


def assert_root_hash(root_hash):
    pass


def add_item(item_json):
    hash = b'sha-256:' + hexlify(hashlib.sha256(item_json).digest())
    print('putting ' + str(hash))
    item_store.Put(hash, item_json)


def append_entry(timestamp, item_hash, key):
    item_data = item_store.Get(item_hash)
    item = json.loads(item_data.decode('utf-8'))

    address = None
    if 'address' in item:
        address = fetch_address(item['address'])

    print(address)

    street_name = None
    if address:
        street_name = address['street']['name']

    end_date = None
    start_date = None
    if 'start-date' in item:
        start_date = dateutil.parser.parse(item['start-date']).date()
    if 'end-date' in item:
        end_date = dateutil.parser.parse(item['end-date']).date()
    with pgconn.cursor() as cursor:
        cursor.execute("INSERT INTO prisons (name, code, address, created_at, updated_at, closed, opened) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (item['name'], item['prison'], street_name, dateutil.parser.parse(timestamp), dateutil.parser.parse(timestamp), end_date, start_date))
        pgconn.commit()


def getcommand(name):
    if (name == b'assert-root-hash'):
        return assert_root_hash
    if (name == b'add-item'):
        return add_item
    if (name == b'append-entry'):
        return append_entry
    raise NameError(b'unknown command: ' + name)


for line in fileinput.input(mode='rb'):
    [command, *args] = line.rstrip().split(b'\t')
    print(command)
    print(args)
    getcommand(command)(*args)

pgconn.close()
