#!/usr/bin/env python3


# new-prisons.py
#
# this script populates the local development postgres instance from the discovery registers
# it works by doing the following:
#   - download the prisons RSF
#   - iterate through each item, store in redis
#   - iterate through each entry, store as a record in redis (using primary key as key):
#   - finally, for each record key:
#     - if there's an address in the item, fetch it from the address register,
#          fetch the corresponding street, and store its name
#     - store in postgres
#   - store the number of entries we've processed in redis so that we don't process them again
#
# This means that each time you run this script, it will only check
# for new entries in the prison register, but it will re-check all
# address and street records in the corresponding registers.
#
# not (yet) done:
#   - check if a record exists in the pg db already before inserting (there's no unique constraint on the prison code field)


from binascii import hexlify, unhexlify
import dateutil.parser
import datetime
import gevent.pool
import grequests
import hashlib
from hashlib import sha256
import json
import os
import psycopg2
import redis


POSTGRES_URL = os.getenv('DATABASE_URL') or "postgresql://localhost/REGISTER_INDEX_SERVICE_DEVELOPMENT"
REDIS_URL = os.getenv('REDIS_URL') or 'redis://localhost:6379'





# this stuff copypasted from philandstuff/verifiable-log-python
# -------------8<--------------------

def split_point(n):
    split = 1;
    while split < n:
        split <<= 1
    return split >> 1


def _branch_hash(l,r):
    h = sha256(b'\x01')
    h.update(l)
    h.update(r)
    return h.digest()


def _rootHashFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot, computeNewRoot, startFromOldRoot):
    if oldSize == newSize:
        if startFromOldRoot:
            # this is the b == true case in RFC 6962
            return oldRoot
        return proofNodes[-1]
    k = split_point(newSize)
    nextHash = proofNodes[-1]
    if oldSize <= k:
        leftChild = _rootHashFromConsistencyProof(oldSize, k, proofNodes[:-1], oldRoot, computeNewRoot, startFromOldRoot)
        if computeNewRoot:
            return _branch_hash(leftChild, nextHash)
        else:
            return leftChild
    else:
        rightChild = _rootHashFromConsistencyProof(oldSize - k, newSize - k, proofNodes[:-1], oldRoot, computeNewRoot, False)
        return _branch_hash(nextHash, rightChild)


def _oldRootFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot):
    return _rootHashFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot, False, True)

def _newRootFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot):
    return _rootHashFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot, True, True)

def validConsistencyProof(oldRoot, newRoot, oldSize, newSize, proofNodes):
    if oldSize == 0: # the empty tree is consistent with any future
        return True
    if oldSize == newSize:
        return oldRoot == newRoot and not proofNodes
    computedOldRoot = _oldRootFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot)
    computedNewRoot = _newRootFromConsistencyProof(oldSize, newSize, proofNodes, oldRoot)
    return oldRoot == computedOldRoot and newRoot == computedNewRoot

# -------------8<--------------------


EMPTY_ROOT_HASH = b'sha-256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'


class RsfProcessor(object):
    def __init__(self):
        self.item_store = redis.StrictRedis.from_url(REDIS_URL)
        self.pgconn = psycopg2.connect(POSTGRES_URL)
        self.pool = gevent.pool.Pool(200) # concurrent requests
        self.entries_processed = int.from_bytes(self.item_store.get(b'entries_processed') or b'', byteorder='big')
        self.root_hash = self.item_store.get(b'root_hash') or EMPTY_ROOT_HASH


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


    def resolve_record(self, cursor, item_hash):
        item_data = self.item_store.get(item_hash)
        item = json.loads(item_data.decode('utf-8'))
        address = None
        if 'address' in item:
            address = self.fetch_address(item['address'])

        street_name = None
        if address:
            street_name = address['street']['name']

        address_uprn = None
        if address:
            address_uprn = address['address']

        end_date = None
        start_date = None
        if 'start-date' in item:
            start_date = dateutil.parser.parse(item['start-date']).date()
        if 'end-date' in item:
            end_date = dateutil.parser.parse(item['end-date']).date()
        cursor.execute("INSERT INTO prisons (name, code, address, created_at, updated_at, closed, opened, address_uprn) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                       (item['name'], item['prison'], street_name, datetime.datetime.now(), datetime.datetime.now(), end_date, start_date, address_uprn))



    def resolve_records(self):
        with self.pgconn.cursor() as cursor:
            cursor.execute("DELETE FROM prisons")
            for code in self.item_store.keys(b'prison:*'):
                item_hash = self.item_store.get(code)
                self.pool.spawn(self.resolve_record, cursor, item_hash)
            self.pool.join()
            self.pgconn.commit()


    def assert_root_hash(self, root_hash):
        self.item_store.set(b'root_hash', root_hash)


    def add_item(self, item_json):
        hash = b'sha-256:' + hexlify(hashlib.sha256(item_json).digest())
        print('putting ' + str(hash))
        self.item_store.set(hash, item_json)


    def append_entry(self, timestamp, item_hash, key):
        self.item_store.set(b'prison:'+key, item_hash)

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


    def close(self, new_root_hash):
        self.pgconn.close()
        # item_store?
        self.item_store.set(b'entries_processed', self.entries_processed.to_bytes(8,byteorder='big'))
        self.item_store.set(b'root_hash', new_root_hash)




def parse_hash(hash_bytestring):
    print(hash_bytestring)
    hash_id, hash_hex = hash_bytestring.split(b':')
    assert hash_id == b'sha-256'
    return unhexlify(hash_hex)


# todo: use total-entries from register_proof_req when that's implemented
# to avoid timing issues where something is minted in between the two requests

register_detail = grequests.map([grequests.get('https://prison.discovery.openregister.org/register.json')])[0].json()
total_entries = register_detail['total-entries']

proc = RsfProcessor()
entries = proc.entries_processed
old_root = proc.root_hash
register_proof_url = 'https://prison.discovery.openregister.org/proof/register/merkle:sha-256'
register_proof_req = grequests.map([grequests.get(register_proof_url)])[0]
new_root = register_proof_req.json()['root-hash'].encode('utf-8')


if entries != 0:
    # check we're starting from a consistent place
    consistency_url = 'https://prison.discovery.openregister.org/proof/consistency/%d/%d/merkle:sha-256' % (entries, total_entries)
    consistency_req = grequests.map([grequests.get(consistency_url)])[0]
    if register_proof_req.status_code != 200:
        print("couldn't get register proof from url %s, got status %d" % (register_proof_url, register_proof_req.status_code))
        exit()
    if consistency_req.status_code != 200:
        print("couldn't get consistency proof from url %s, got status %d" % (consistency_url, consistency_req.status_code))
        exit()

    print(consistency_req.json())
    print(entries, total_entries, old_root, new_root)
    print("proof result: " + str(validConsistencyProof(parse_hash(old_root), parse_hash(new_root), entries, total_entries, [parse_hash(node.encode('utf-8')) for node in consistency_req.json()['merkle-consistency-nodes']])))

    proof_is_valid = validConsistencyProof(
        parse_hash(old_root),
        parse_hash(new_root),
        entries,
        total_entries,
        [parse_hash(node.encode('utf-8')) for node in consistency_req.json()['merkle-consistency-nodes']])

    if not proof_is_valid:
        # consistency proof failed, we should start again from the beginning
        # because the data might have changed
        print('consistency proof failed, starting again from the beginning')
        entries = 0


print('starting from entry %d' % entries)
rsf_url = 'https://prison.discovery.openregister.org/download-rsf/%d/%d' % (entries, total_entries)
rsf_req = grequests.map([grequests.get(rsf_url, stream=True)])[0]
if rsf_req.status_code != 200:
    print("couldn't get RSF from url %s, got status %d" % (rsf_url, rsf_req.status_code))
    exit()

rsf_req.encoding = 'utf-8'

for line in rsf_req.iter_lines():
    [command, *args] = line.rstrip().split(b'\t')
    proc.process(command,args)

proc.resolve_records()
proc.close(new_root)
