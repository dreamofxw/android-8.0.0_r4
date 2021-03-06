# Copyright (c) 2014 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# This file defines helper functions for putting entries into elasticsearch.

"""Utils for sending metadata to elasticsearch

Elasticsearch is a key-value store NOSQL database.
Source is here: https://github.com/elasticsearch/elasticsearch
We will be using es to store our metadata.

For example, if we wanted to store the following metadata:

metadata = {
    'host_id': 1
    'job_id': 20
    'time_start': 100000
    'time_recorded': 100006
}

The following call will send metadata to the default es server.
    es_utils.ESMetadata().post(index, metadata)
We can also specify which port and host to use.

Using for testing: Sometimes, when we choose a single index
to put entries into, we want to clear that index of all
entries before running our tests. Use clear_index function.
(see es_utils_functionaltest.py for an example)

This file also contains methods for sending queries to es. Currently,
the query (json dict) we send to es is quite complicated (but flexible).
We've included several methods that composes queries that would be useful.
These methods are all named create_*_query()

For example, the below query returns job_id, host_id, and job_start
for all job_ids in [0, 99999] and host_id matching 10.

range_eq_query = {
    'fields': ['job_id', 'host_id', 'job_start'],
    'query': {
        'filtered': {
            'query': {
                'match': {
                    'host_id': 10,
                }
            }
            'filter': {
                'range': {
                    'job_id': {
                        'gte': 0,
                        'lte': 99999,
                    }
                }
            }
        }
    }
}

To send a query once it is created, call execute_query() to send it to the
intended elasticsearch server.

"""

import collections
import json
import logging
import socket
import time

try:
    import elasticsearch
    from elasticsearch import helpers as elasticsearch_helpers
except ImportError:
    logging.debug('Failed to import elasticsearch. Mock classes will be used '
                  'and calls to Elasticsearch server will be no-op. Test run '
                  'is not affected by the missing elasticsearch module.')
    import elasticsearch_mock as elasticsearch
    elasticsearch_helpers = elasticsearch.Elasticsearch()


# Global timeout for connection to esdb timeout.
DEFAULT_TIMEOUT = 30

# Default result size for a query.
DEFAULT_RESULT_SIZE = 10**4
# Default result size when scrolling query results.
DEFAULT_SCROLL_SIZE = 5*10**4

class EsUtilException(Exception):
    """Exception raised when functions here fail. """
    pass


QueryResult = collections.namedtuple('QueryResult', ['total', 'hits'])


class ESMetadata(object):
    """Class handling es connection for metadata."""

    @property
    def es(self):
        """Read only property, lazily initialized"""
        if not self._es:
            self._es = elasticsearch.Elasticsearch(host=self.host,
                                                   port=self.port,
                                                   timeout=self.timeout)
        return self._es


    def __init__(self, use_http, host, port, index, udp_port,
                 timeout=DEFAULT_TIMEOUT):
        """Initialize ESMetadata object.

        @param use_http: Whether to send data to ES using HTTP.
        @param host: Elasticsearch host.
        @param port: Elasticsearch port.
        @param index: What index the metadata is stored in.
        @param udp_port: What port to use for UDP data.
        @param timeout: How long to wait while connecting to es.
        """
        self.use_http = use_http
        self.host = host
        self.port = port
        self.index = index
        self.udp_port = udp_port
        self.timeout = timeout
        self._es = None


    def _send_data_http(self, type_str, metadata):
        """Sends data to insert into elasticsearch using HTTP.

        @param type_str: sets the _type field in elasticsearch db.
        @param metadata: dictionary object containing metadata
        """
        try:
            self.es.index(index=self.index, doc_type=type_str, body=metadata)
        except elasticsearch.ElasticsearchException as e:
            # Mute exceptions from metadata reporting to prevent meta data
            # reporting errors from killing test.
            logging.error(e)


    def _send_data_udp(self, type_str, metadata):
        """Sends data to insert into elasticsearch using UDP.

        @param type_str: sets the _type field in elasticsearch db.
        @param metadata: dictionary object containing metadata
        """
        try:
            # Header.
            message = json.dumps(
                    {'index': {'_index': self.index, '_type': type_str}},
                    separators=(', ', ' : '))
            message += '\n'
            # Metadata.
            message += json.dumps(metadata, separators=(', ', ' : '))
            message += '\n'

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(message, (self.host, self.udp_port))
        except socket.error as e:
            logging.warn(e)


    def post(self, type_str, metadata, log_time_recorded=True, **kwargs):
        """Wraps call of send_data, inserts entry into elasticsearch.

        @param type_str: Sets the _type field in elasticsearch db.
        @param metadata: Dictionary object containing metadata
        @param log_time_recorded: Whether to automatically record the time
                                  this metadata is recorded. Default is True.
        @param kwargs: Additional metadata fields

        @return: True if post action succeeded. Otherwise return False.

        """
        if not metadata:
            return True

        metadata = metadata.copy()
        metadata.update(kwargs)
        # metadata should not contain anything with key '_type'
        if '_type' in metadata:
            type_str = metadata['_type']
            del metadata['_type']
        if log_time_recorded:
            metadata['time_recorded'] = time.time()
        try:
            if self.use_http:
                self._send_data_http(type_str, metadata)
            else:
                self._send_data_udp(type_str, metadata)
            return True
        except elasticsearch.ElasticsearchException as e:
            logging.error(e)
            return False


    def bulk_post(self, data_list, log_time_recorded=True, **kwargs):
        """Wraps call of send_data, inserts entry into elasticsearch.

        @param data_list: A list of dictionary objects containing metadata.
        @param log_time_recorded: Whether to automatically record the time
                                  this metadata is recorded. Default is True.
        @param kwargs: Additional metadata fields

        @return: True if post action succeeded. Otherwise return False.

        """
        if not data_list:
            return True

        actions = []
        for metadata in data_list:
            metadata = metadata.copy()
            metadata.update(kwargs)
            if log_time_recorded and not 'time_recorded' in metadata:
                metadata['time_recorded'] = time.time()
            metadata['_index'] = self.index
            actions.append(metadata)

        try:
            elasticsearch_helpers.bulk(self.es, actions)
            return True
        except elasticsearch.ElasticsearchException as e:
            logging.error(e)
            return False


    def _compose_query(self, equality_constraints=[], fields_returned=None,
                       range_constraints=[], size=DEFAULT_RESULT_SIZE,
                       sort_specs=None, regex_constraints=[],
                       batch_constraints=[]):
        """Creates a dict. representing multple range and/or equality queries.

        Example input:
        _compose_query(
            fields_returned = ['time_recorded', 'hostname',
                               'status', 'dbg_str'],
            equality_constraints = [
                ('_type', 'host_history'),
                ('hostname', '172.22.169.106'),
            ],
            range_constraints = [
                ('time_recorded', 1405628341.904379, 1405700341.904379)
            ],
            size=20,
            sort_specs=[
                'hostname',
                {'time_recorded': 'asc'},
            ]
        )

        Output:
        {
            'fields': ['time_recorded', 'hostname', 'status', 'dbg_str'],
            'query': {
                'bool': {
                    'minimum_should_match': 3,
                    'should': [
                        {
                            'term':  {
                                '_type': 'host_history'
                            }
                        },
                        {
                            'term': {
                                'hostname': '172.22.169.106'
                            }
                        },
                        {
                            'range': {
                                'time_recorded': {
                                    'gte': 1405628341.904379,
                                    'lte': 1405700341.904379
                                }
                            }
                        }
                    ]
                },
            },
            'size': 20
            'sort': [
                'hostname',
                { 'time_recorded': 'asc'},
            ]
        }

        @param equality_constraints: list of tuples of (field, value) pairs
            representing what each field should equal to in the query.
            e.g. [ ('field1', 1), ('field2', 'value') ]
        @param fields_returned: list of fields that we should return when
            the query is executed. Set it to None to return all fields. Note
            that the key/vals will be stored in _source key of the hit object,
            if fields_returned is set to None.
        @param range_constraints: list of tuples of (field, low, high) pairs
            representing what each field should be between (inclusive).
            e.g. [ ('field1', 2, 10), ('field2', -1, 20) ]
            If you want one side to be unbounded, you can use None.
            e.g. [ ('field1', 2, None) ] means value of field1 >= 2.
        @param size: max number of entries to return. Default is 100000.
        @param sort_specs: A list of fields to sort on, tiebreakers will be
            broken by the next field(s).
        @param regex_constraints: A list of regex constraints of tuples of
            (field, value) pairs, e.g., [('filed1', '.*value.*')].
        @param batch_constraints: list of tuples of (field, list) pairs
            representing each field should be equal to one of the values
            in the list.
            e.g., [ ('job_id', [10, 11, 12, 13]) ]
        @returns: dictionary object that represents query to es.
                  This will return None if there are no equality constraints
                  and no range constraints.
        """
        if not equality_constraints and not range_constraints:
            raise EsUtilException('No range or equality constraints specified.')

        # Creates list of range dictionaries to put in the 'should' list.
        range_list = []
        if range_constraints:
            for key, low, high in range_constraints:
                if low is None and high is None:
                    continue
                temp_dict = {}
                if low is not None:
                    temp_dict['gte'] = low
                if high is not None:
                    temp_dict['lte'] = high
                range_list.append( {'range': {key: temp_dict}})

        # Creates the list of term dictionaries to put in the 'should' list.
        eq_list = [{'term': {k: v}} for k, v in equality_constraints if k]
        batch_list = [{'terms': {k: v}} for k, v in batch_constraints if k]
        regex_list = [{'regexp': {k: v}} for k, v in regex_constraints if k]
        constraints = eq_list + batch_list + range_list + regex_list
        query = {
            'query': {
                'bool': {
                    'must': constraints,
                }
            },
        }
        if fields_returned:
            query['fields'] = fields_returned
        query['size'] = size
        if sort_specs:
            query['sort'] = sort_specs
        return query


    def execute_query(self, query):
        """Makes a query on the given index.

        @param query: query dictionary (see _compose_query)
        @returns: A QueryResult instance describing the result.

        Example output:
        {
            "took" : 5,
            "timed_out" : false,
            "_shards" : {
                "total" : 16,
                "successful" : 16,
                "failed" : 0
            },
            "hits" : {
                "total" : 4,
                "max_score" : 1.0,
                "hits" : [ {
                    "_index" : "graphite_metrics2",
                    "_type" : "metric",
                    "_id" : "rtntrjgdsafdsfdsfdsfdsfdssssssss",
                    "_score" : 1.0,
                    "_source":{"target_type": "timer",
                               "host_id": 1,
                               "job_id": 22,
                               "time_start": 400}
                }, {
                    "_index" : "graphite_metrics2",
                    "_type" : "metric",
                    "_id" : "dfgfddddddddddddddddddddddhhh",
                    "_score" : 1.0,
                    "_source":{"target_type": "timer",
                        "host_id": 2,
                        "job_id": 23,
                        "time_start": 405}
                }, {
                "_index" : "graphite_metrics2",
                "_type" : "metric",
                "_id" : "erwerwerwewtrewgfednvfngfngfrhfd",
                "_score" : 1.0,
                "_source":{"target_type": "timer",
                           "host_id": 3,
                           "job_id": 24,
                           "time_start": 4098}
                }, {
                    "_index" : "graphite_metrics2",
                    "_type" : "metric",
                    "_id" : "dfherjgwetfrsupbretowegoegheorgsa",
                    "_score" : 1.0,
                    "_source":{"target_type": "timer",
                               "host_id": 22,
                               "job_id": 25,
                               "time_start": 4200}
                } ]
            }
        }

        """
        if not self.es.indices.exists(index=self.index):
            logging.error('Index (%s) does not exist on %s:%s',
                          self.index, self.host, self.port)
            return None
        result = self.es.search(index=self.index, body=query)
        # Check if all matched records are returned. It could be the size is
        # set too small. Special case for size set to 1, as that means that
        # the query cares about the first matched entry.
        # TODO: Use pagination in Elasticsearch. This needs major change on how
        #       query results are iterated.
        size = query.get('size', 1)
        need_scroll = 'size' in query and size == DEFAULT_RESULT_SIZE
        return_count = len(result['hits']['hits'])
        total_match = result['hits']['total']
        if total_match > return_count and need_scroll:
            logging.warn('There are %d matched records, only %d entries are '
                         'returned. Query size is set to %d. Will try to use '
                         'scroll command to get all entries.', total_match,
                         return_count, size)
            # Try to get all results with scroll.
            hits = self._get_results_by_scan(query, total_match)
        else:
            hits = result['hits']['hits']
        # Extract the actual results from the query.
        output = QueryResult(total_match, [])
        for hit in hits:
            converted = {}
            if 'fields' in hit:
                for key, value in hit['fields'].items():
                    converted[key] = value[0] if len(value)==1 else value
            else:
                converted = hit['_source'].copy()
            output.hits.append(converted)
        return output


    def _get_results_by_scan(self, query, total_match=None):
        """Get all results by using scan.

        @param query: query dictionary (see _compose_query)
        @param total_match: The number of total matched results. Pass the value
                in so the code doesn't need to run another query to get it.

        @returns: A list of matched results.
        """
        if True or not total_match:
            # Reduce the return size to make the query run faster.
            query['size'] = 1
            result = self.es.search(index=self.index, body=query)
            total_match = result['hits']['total']
        # Remove the sort from query so scroll method can run faster.
        sort = None
        if 'sort' in query:
            sort = query['sort']
            if len(sort) > 1:
                raise EsUtilException('_get_results_by_scan does not support '
                                      'sort with more than one key: %s', sort)
            del query['sort']
        del query['size']
        scroll = elasticsearch_helpers.scan(self.es, query=query,
                                            index=self.index,
                                            size=DEFAULT_SCROLL_SIZE)
        hits = []
        next_mark = 0
        for hit in scroll:
          hits.append(hit)
          downloaded_percent = 100 * float(len(hits))/total_match
          if downloaded_percent > next_mark:
              logging.debug('%2.0f%% downloaded (%d)', downloaded_percent,
                            len(hits))
              next_mark += 5
        logging.debug('Number of hits found: %s', len(hits))

        if sort:
            logging.debug('Sort hits with rule: %s', sort)
            sort_key = sort[0].keys()[0]
            is_desc = sort[0].values()[0] == 'desc'
            # If the query has `fields` specified, the dict of hit stores value
            # in hit['fields'], otherwise, the keyvals are stored in
            # hit['_source'].
            key = lambda hit:(hit['_source'][sort_key] if '_source' in hit else
                              hit['fields'][sort_key][0])
            hits = sorted(hits, key=key, reverse=is_desc)

        return hits


    def query(self, *args, **kwargs):
        """The arguments to this function are the same as _compose_query."""
        query = self._compose_query(*args, **kwargs)
        return self.execute_query(query)
