"""Minimal in-memory stand-ins for boto3 DynamoDB / S3 / Polly, so the API runs offline in tests."""
import io
import sys
import types


class _Cond:
    def __init__(self, name):
        self.name = name

    def eq(self, v):
        return ("eq", self.name, v)


class Table:
    def __init__(self, name):
        self.name, self.items, self.keys = name, {}, None

    def _key(self, item):
        return tuple(str(item[k]) for k in self.keys)

    def put_item(self, Item):
        self.items[self._key(Item)] = dict(Item)

    def get_item(self, Key):
        it = self.items.get(self._key(Key))
        return {"Item": dict(it)} if it else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None):
        it = self.items[self._key(Key)]
        names = ExpressionAttributeNames or {}
        for part in UpdateExpression.replace("SET ", "").split(","):
            left, right = [x.strip() for x in part.split("=", 1)]
            left = names.get(left, left)
            if right.startswith("if_not_exists"):
                it[left] = it.get(left, 0) + 1
            else:
                it[left] = ExpressionAttributeValues[right]

    def query(self, KeyConditionExpression, IndexName=None, ExclusiveStartKey=None):
        _, field, value = KeyConditionExpression
        return {"Items": [dict(i) for i in self.items.values() if str(i.get(field)) == str(value)]}

    def batch_writer(self, overwrite_by_pkeys=None):
        table = self

        class BW:
            def __enter__(self):
                return table

            def __exit__(self, *a):
                return False
        return BW()


KEYS = {"grid": ["cellId", "hourKey"], "shifts": ["riderId", "shiftId"],
        "pings": ["shiftId", "ts"], "points": ["pointId"]}
TABLES = {}


class Resource:
    def Table(self, name):
        if name not in TABLES:
            TABLES[name] = Table(name)
            TABLES[name].keys = KEYS[name]
        return TABLES[name]


S3 = {}


class Client:
    def __init__(self, svc):
        self.svc = svc

    def get_object(self, Bucket, Key):
        if Key not in S3:
            raise KeyError(Key)
        return {"Body": io.BytesIO(S3[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        S3[Key] = Body.encode() if isinstance(Body, str) else Body

    def synthesize_speech(self, **kw):
        return {"AudioStream": io.BytesIO(b"ID3fake-mp3-" + kw["Text"].encode())}


def install():
    boto3 = types.ModuleType("boto3")
    boto3.resource = lambda svc: Resource()
    boto3.client = lambda svc, **kw: Client(svc)
    dynamodb = types.ModuleType("boto3.dynamodb")
    conditions = types.ModuleType("boto3.dynamodb.conditions")
    conditions.Key = _Cond
    sys.modules.update({"boto3": boto3, "boto3.dynamodb": dynamodb, "boto3.dynamodb.conditions": conditions})
