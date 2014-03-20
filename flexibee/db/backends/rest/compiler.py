import datetime
import sys
import requests


from django.db.models.sql.constants import MULTI, SINGLE
from django.db.models.sql.where import AND, OR
from django.db.utils import DatabaseError, IntegrityError
from django.utils.tree import Node

from functools import wraps

from djangotoolbox.db.basecompiler import NonrelQuery, NonrelCompiler, \
    NonrelInsertCompiler, NonrelUpdateCompiler, NonrelDeleteCompiler
from flexibee.db.backends.rest.connection import RestQuery
from django.db.models.fields import Field
from django.db.models.fields.related import RelatedField
from django.db.models.sql.subqueries import UpdateQuery

# TODO: Change this to match your DB
# Valid query types (a dictionary is used for speedy lookups).
OPERATORS_MAP = {
    'exact': '=',
    'gt': '>',
    'gte': '>=',
    'lt': '<',
    'lte': '<=',
    'in': 'in',
    'isnull': 'is null',
    'like': 'like',
    'startswith': 'begins',
    'endswith': 'ends',
}


def safe_call(func):
    @wraps(func)
    def _func(*args, **kwargs):
        # try:
        return func(*args, **kwargs)
        # TODO: Replace this with your DB error class
        # except YourDatabaseError, e:
        #    raise DatabaseError, DatabaseError(*tuple(e)), sys.exc_info()[2]
    return _func


class BackendQuery(NonrelQuery):

    def __init__(self, compiler, fields):
        super(BackendQuery, self).__init__(compiler, fields)
        self.connector = self.connection.connector
        self.db_query = RestQuery(self.connection.connector, self.query.model._meta.db_table,
                                  [field.db_column or field.get_attname() for field in fields])
        self.related_field_names = self._related_field_names()

    # This is needed for debugging
    def __repr__(self):
        # TODO: add some meaningful query string for debugging
        return '<BackendQuery: ...>'

    def _related_field_names(self):
        related_fields = []
        for field in self.fields:
            if isinstance(field, RelatedField):
                related_fields.append(field.db_column or field.get_attname())
        return related_fields

    @safe_call
    def fetch(self, low_mark=0, high_mark=None):
        if high_mark == None:
            base = None
        else:
            base = high_mark - low_mark


        for entity in self.db_query.fetch(low_mark, base):
            for related_field in self.related_field_names:
                entity[related_field] = entity['%s@ref' % related_field].split('/')[-1][:-5]
            yield entity

    @safe_call
    def count(self, limit=None):
        return self.db_query.count()

    @safe_call
    def delete(self):
        self.db_query.delete()

    @safe_call
    def update(self, data):
        self.db_query.update(data)

    @safe_call
    def order_by(self, ordering):
        if isinstance(ordering, (list, tuple)):
            for field, is_asc in ordering:
                self.db_query.add_ordering(field.db_column or field.get_attname(), is_asc)


    # This function is used by the default add_filters() implementation which
    # only supports ANDed filter rules and simple negation handling for
    # transforming OR filters to AND filters:
    # NOT (a OR b) => (NOT a) AND (NOT b)
    @safe_call
    def add_filter(self, field, lookup_type, negated, value):
        try:
            op = OPERATORS_MAP[lookup_type]
        except KeyError:
            raise DatabaseError("Lookup type %r isn't supported" % lookup_type)

        # Handle special-case lookup types
        if callable(op):
            op, value = op(lookup_type, value)

        db_value = self.compiler.convert_value_for_db(field.get_internal_type(), value)
        self.db_query.add_filter(field.db_column or field.get_attname(), op, db_value, negated)


class SQLCompiler(NonrelCompiler):
    query_class = BackendQuery

    # This gets called for each field type when you fetch() an entity.
    # db_type is the string that you used in the DatabaseCreation mapping
    def convert_value_from_db(self, db_type, value):
        # TODO: implement this

        # Handle list types
        if isinstance(value, (list, tuple)) and len(value) and \
                db_type.startswith('ListField:'):
            db_sub_type = db_type.split(':', 1)[1]
            value = [self.convert_value_from_db(db_sub_type, subvalue)
                     for subvalue in value]
        elif isinstance(value, str):
            # Always retrieve strings as unicode
            value = value.decode('utf-8')
        return value

    # This gets called for each field type when you insert() an entity.
    # db_type is the string that you used in the DatabaseCreation mapping
    def convert_value_for_db(self, db_type, value):
        # TODO: implement this

        if isinstance(value, unicode):
            value = '\'%s\'' % unicode(value)
        elif isinstance(value, str):
            # Always store strings as unicode
            value = value = '\'%s\'' % value.decode('utf-8')
        elif isinstance(value, (list, tuple)) and len(value) and \
                db_type.startswith('ListField:'):
            db_sub_type = db_type.split(':', 1)[1]
            value = [self.convert_value_for_db(db_sub_type, subvalue)
                     for subvalue in value]
        return value


# This handles both inserts and updates of individual entities
class SQLInsertCompiler(NonrelInsertCompiler, SQLCompiler):

    @safe_call
    def insert(self, data, return_id=False):
        db_query = RestQuery(self.connection.connector, self.query.model._meta.db_table)
        pk = db_query.insert(data)
        return pk


class SQLUpdateCompiler(NonrelUpdateCompiler, SQLCompiler):

    @safe_call
    def update(self, values):
        db_values = {}

        for field, value in values:
            db_value = self.convert_value_for_db(field.get_internal_type(), value)
            db_field = field.db_column or field.get_attname()
            db_values[db_field] = db_value

        query = self.build_query([self.query.model._meta.pk]).update(db_values)


class SQLDeleteCompiler(NonrelDeleteCompiler, SQLCompiler):
    pass
