# SPDX-License-Identifier: GPL-3.0+

from __future__ import unicode_literals
import re
from datetime import datetime
from six import text_type

from neomodel import UniqueIdProperty, db

from purview import log
from purview.error import ValidationError


def timestamp_to_datetime(timestamp):
    """
    Convert a string timestamp to a datetime object.

    :param str timestamp: a generic or ISO-8601 timestamp
    :return: datetime object of the timestamp
    :rtype: datetime.datetime
    :raises ValueError: if the timestamp is an unsupported or invalid format
    """
    log.debug('Trying to parse the timestamp "{0}"'.format(timestamp))
    error_msg = 'The timestamp "{0}" is an invalid format'.format(timestamp)
    combinations = (
        (r'^(?P<datetime>\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{1,2}:\d{1,2})(?:\.\d+)?$',
         '%Y-%m-%d %H:%M:%S'),
        (r'^(?P<datetime>\d{4}-\d{1,2}-\d{1,2})$', '%Y-%m-%d'),
        # ISO 8601 format
        (r'^(?P<datetime>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|[-+]00(?::00)?)?$',
         '%Y-%m-%dT%H:%M:%S'))

    for combination in combinations:
        regex_match = re.match(combination[0], timestamp)
        if regex_match:
            try:
                return datetime.strptime(regex_match.group('datetime'), combination[1])
            except ValueError:
                # In case the user asked for an unreleastic date like "2020:99:99"
                raise ValueError(error_msg)

    raise ValueError(error_msg)


def str_to_bool(item):
    """
    Convert a string to a boolean.

    :param str item: string to parse
    :return: a boolean equivalent
    :rtype: boolean
    """
    if isinstance(item, text_type):
        return item.lower() in ('true', '1')
    else:
        return False


def inflate_node(result):
    """
    Inflate a Neo4j result to a neomodel model object.

    :param neo4j.v1.types.Node result: a node from a cypher query result
    :return: a model (PurviewStructuredNode) object
    """
    # To prevent a ciruclar import, this must be imported here
    from purview.models import names_to_model

    for label in result.labels:
        if label in names_to_model:
            node_model = names_to_model[label]
            break
    else:
        # This should never happen unless Neo4j returns labels that aren't associated with
        # classes in all_models
        RuntimeError('A StructuredNode couldn\'t be found from the labels: {0}'.format(
            ', '.join(result.labels)))

    return node_model.inflate(result)


def get_neo4j_node(resource_name, uid):
    """
    Get a Neo4j node based on a label and unique identifier.

    :param str resource_name: a neomodel model label
    :param str uid: a string of the unique identifier defined in the neomodel model
    :return: a neomodel model object
    :raises ValidationError: if the requested resource doesn't exist or doesn't have a
    UniqueIdProperty
    """
    # To prevent a ciruclar import, we must import this here
    from purview.models import all_models

    for model in all_models:
        if model.__label__.lower() == resource_name.lower():
            for prop_name, prop_def in model.defined_properties().items():
                if isinstance(prop_def, UniqueIdProperty):
                    return model.nodes.get_or_none(**{prop_def.name: uid})

    # Some models don't have unique ID's and those should be skipped
    models_wo_uid = ('DistGitRepo', 'DistGitBranch')
    model_names = [model.__name__.lower() for model in all_models
                   if model.__name__ not in models_wo_uid]
    error = ('The requested resource "{0}" is invalid. Choose from the following: '
             '{1}, and {2}.'.format(resource_name, ', '.join(model_names[:-1]), model_names[-1]))
    raise ValidationError(error)


def node_query(node_label, uid_name=None, uid=None):
    """
    Build part of a raw cypher query for a node label.

    :param str node_label: a Neo4j node label
    :kwarg str uid_name: name of node's UniqueIdProperty
    :kwarg str uid: value of node's UniqueIdProperty
    :return: the node represented in raw cypher
    :rtype: str
    """
    if uid_name and uid:
        return '({0}:{1} {{{2}:"{3}"}})'.format(node_label.lower(), node_label,
                                                uid_name.rstrip('_'), uid)
    return '({0}:{1})'.format(node_label.lower(), node_label)


def create_query(item, uid_name, uid, reverse=False):
    """
    Create a raw cypher query for a node label.

    :param node item: a neo4j node whose story is requested by the user
    :param str uid_name: name of node's UniqueIdProperty
    :param str uid: value of node's UniqueIdProperty
    :param bool reverse: boolean value to specify the direction to proceed
    from current node corresponding to the story_flow
    :return: a string containing raw cypher query to retrieve the story of an artifact from neo4j
    :rtype: str
    """
    # To avoid circular imports
    from purview.models import story_flow

    query = ''

    if reverse is True:
        rel_label = 'backward_relationship'
        node_label = 'backward_label'
    else:
        rel_label = 'forward_relationship'
        node_label = 'forward_label'

    curr_node_label = item.__label__
    while story_flow[curr_node_label][rel_label]:
        if curr_node_label == item.__label__:
            node = node_query(curr_node_label, uid_name, uid)
        else:
            node = node_query(curr_node_label)

        next_node = node_query(story_flow[curr_node_label][node_label])
        query += 'OPTIONAL MATCH {0}-[:{1}]->{2}\n'.format(
            node, story_flow[curr_node_label][rel_label], next_node)

        curr_node_label = story_flow[curr_node_label][node_label]

    if query:
        query += 'RETURN *'

    return query


def query_neo4j(query):
    """
    Query neo4j and serialize the results.

    :param str query: raw cypher query
    :return results_dict: a dictionary containing serialized results received from neo4j
    :rtype: dict
    """
    results_dict = {}
    results, _ = db.cypher_query(query)

    if not results:
        return results_dict

    for node in results[0]:
        if node:
            inflated_node = inflate_node(node)
            node_label = inflated_node.__label__
            if node_label not in results_dict:
                results_dict[node_label] = []
            results_dict[node_label].append(inflated_node.serialized)
    return results_dict
