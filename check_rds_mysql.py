#!/usr/bin/env python3
"""
check_cloudwatch_metric.py

An Icinga/Nagios plug-in to check RDS mysql database health parameters

Author: Frank Wittig <frank@e5k.de>
Source: https://github.com/elias5000/check_rds_mysql
"""

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import BotoCoreError, ClientError

STATE_OK = 0
STATE_WARN = 1
STATE_CRIT = 2
STATE_UNKNOWN = 3


class DbInstance:
    """ Database instance object """

    def __init__(self, region, db_instance_identifier):
        self.region = region
        self.db_instance_identifier = db_instance_identifier

        rds = boto3.session.Session(region_name=self.region).client('rds')
        self.data = \
            rds.describe_db_instances(DBInstanceIdentifier=self.db_instance_identifier)[
                'DBInstances'][
                0]
        self.parameters = self.fetch_parameters()
        self.instance_class = self.fetch_instance_class()

    @property
    def parameter_group_name(self):
        return self.data['DBParameterGroups'][0]['DBParameterGroupName']

    def fetch_parameters(self):
        rds = boto3.session.Session(region_name=self.region).client('rds')
        paginator = rds.get_paginator('describe_db_parameters')
        res = paginator.paginate(DBParameterGroupName=self.parameter_group_name)
        params = {}
        for page in res:
            for param in page['Parameters']:
                params[param['ParameterName']] = param
        return params

    def parameter(self, which):
        try:
            return self.parameters[which]['ParameterValue']
        except KeyError:
            return False

    @property
    def instance_class_name(self):
        return self.data['DBInstanceClass']

    def fetch_instance_class(self):
        instance_class_name = self.instance_class_name
        if instance_class_name.startswith('db.'):
            instance_class_name = '.'.join(self.instance_class_name.split('.')[1:])
        ec2 = boto3.client('ec2')
        return ec2.describe_instance_types(InstanceTypes=[instance_class_name])['InstanceTypes'][0]

    @property
    def instance_memory(self):
        return self.instance_class['MemoryInfo']['SizeInMiB'] * 1024 * 1024

    @property
    def max_connections(self):
        value = self.parameter('max_connections')
        if value.startswith('{'):
            param, divisor = value.split('{')[1].split('}')[0].split('/')
            if param == 'DBInstanceClassMemory':
                return int(self.instance_memory / int(divisor))
            raise RuntimeError('Cannot compute value "%2"', value)
        return int(value)

    @property
    def storage(self):
        return self.data['AllocatedStorage'] * 1024 * 1024 * 1024


class Metric:
    """ CloudWatch metric object """

    # pylint: disable=too-many-instance-attributes
    def __init__(self, **kwargs):
        self.dimensions = kwargs.get('dimensions')
        self.last_state = kwargs.get('last_state')
        self.minutes = int(kwargs.get('minutes'))
        self.name = kwargs.get('name')
        self.namespace = kwargs.get('namespace')
        self.prefix = kwargs.get('prefix', 'AWS')
        self.region = kwargs.get('region', 'eu-central-1')
        self.statistics = kwargs.get('statistics', 'Average')

    def get_client(self):
        """
        Return cloudwatch client for region
        :return:
        """
        return boto3.session.Session(region_name=self.region).resource('cloudwatch')

    def get_metric(self):
        """
        Return metric resource by name
        :return:
        """
        return self.get_client().Metric("{}/{}".format(self.prefix, self.namespace), self.name)

    def get_dimensions(self):
        """
        Return dimensions for request
        :return:
        """
        if not self.dimensions:
            return []

        dimensions = []
        for pair in self.dimensions.split(','):
            bits = pair.split(':')
            dimensions.append({
                'Name': bits[0],
                'Value': bits[1]
            })
        return dimensions

    def get_statistics(self, metric=None, offset=0):
        """
        Return statistics for resource
        :param metric:
        :param offset:
        :return:
        """
        if offset > 20:
            return None

        if metric is None:
            metric = self.get_metric()

        try:
            statistics = metric.get_statistics(
                Dimensions=self.get_dimensions(),
                StartTime=self.start_time(offset),
                EndTime=self.end_time(offset),
                Period=300,
                Statistics=[self.statistics]
            )
        except (BotoCoreError, ClientError) as err:
            print("UNKNOWN - {}".format(err))
            sys.exit(STATE_UNKNOWN)

        if not statistics['Datapoints'] and self.last_state:
            statistics = self.get_statistics(metric, offset + 1)

        return statistics

    def get_current_value(self):
        """
        Return latest value from statistics
        :return:
        """
        statistics = self.get_statistics()
        if not statistics['Datapoints']:
            return None

        return statistics['Datapoints'][-1][self.statistics]

    def start_time(self, offset=0):
        """
        Return start time
        :param offset:
        :return:
        """
        return datetime.utcnow() - timedelta(minutes=(self.minutes + offset))

    @staticmethod
    def end_time(offset=0):
        """
        Return end time
        :param offset:
        :return:
        """
        return datetime.utcnow() - timedelta(minutes=offset)


def compare_range(value, window):
    """
    Compare value with nagios range and return True if value is within boundaries
    :param value:
    :param window:
    :return:
    """
    incl = False
    if window[0] == '@':
        incl = True
        window = window[1:]

    if ":" not in window:
        start = 0
        stop = window
    else:
        bits = window.split(':')
        start = bits[0]
        stop = bits[1] if bits[1] else '~'

    start = None if start == '~' else float(start)
    stop = None if stop == '~' else float(stop)
    if start is not None and ((incl and value <= start) or (not incl and value < start)):
        return False
    if stop is not None and ((incl and value >= stop) or (not incl and value > stop)):
        return False

    return True


def compare(value, warn, crit):
    """
    Compare value with thresholds and return status
    :param value:
    :param warn:
    :param crit:
    :return:
    """
    if not compare_range(value, crit):
        return STATE_CRIT
    if not compare_range(value, warn):
        return STATE_WARN
    return STATE_OK


def unused_connections(args):
    """
    Return available connections
    :param args:
    :return:
    """
    instance = DbInstance(args.region, args.instance)
    connections_metric = Metric(
        dimensions='DBInstanceIdentifier:{}'.format(args.instance),
        last_state=args.last_state,
        minutes=5,
        name='DatabaseConnections',
        namespace='RDS',
        prefix='AWS',
        region=args.region,
        statistics='Minimum'
    )
    value = instance.max_connections - connections_metric.get_current_value()
    if args.percent:
        value = value / instance.max_connections * 100
    return value


def free_storage(args):
    """
    Return free storage
    :param args:
    :return:
    """
    instance = DbInstance(args.region, args.instance)
    connections_metric = Metric(
        dimensions='DBInstanceIdentifier:{}'.format(args.instance),
        last_state=args.last_state,
        minutes=5,
        name='FreeStorageSpace',
        namespace='RDS',
        prefix='AWS',
        region=args.region,
        statistics='Minimum'
    )
    value = connections_metric.get_current_value()
    if args.percent:
        value = value / instance.storage * 100
    return value


def cpu_used(args):
    """
    Return cpu used
    :param args:
    :return:
    """
    connections_metric = Metric(
        dimensions='DBInstanceIdentifier:{}'.format(args.instance),
        last_state=args.last_state,
        minutes=5,
        name='CPUUtilization',
        namespace='RDS',
        prefix='AWS',
        region=args.region,
        statistics='Maximum'
    )
    return connections_metric.get_current_value()


def expand_unit(value):
    """
    Expand units of bytes (K, Ki, M, Mi, G, Gi)
    :param value:
    :return:
    """
    if ":" in value:
        return ":".join(str(expand_unit(val)) for val in value.split(':'))
    if value.endswith('K'):
        return int(value[:-1]) * 1000
    if value.endswith('Ki'):
        return int(value[:-2]) * 1024
    if value.endswith('M'):
        return int(value[:-1]) * 1000000
    if value.endswith('Mi'):
        return int(value[:-2]) * 1048576
    if value.endswith('G'):
        return int(value[:-1]) * 1000000000
    if value.endswith('Gi'):
        return int(value[:-2]) * 1073741824
    return value


def main():
    """ CLI user interface """
    parser = ArgumentParser(
        formatter_class=RawDescriptionHelpFormatter,
        epilog="""\
thresholds and ranges:
  Threshold ranges are in Nagios format:
  https://nagios-plugins.org/doc/guidelines.html#THRESHOLDFORMAT
  For disk threshold you can specify a unit (e.g. "1000Mi:", "8Gi")\
"""
    )
    required = parser.add_argument_group('required arguments')
    required.add_argument('--warn-cpu', help='cpu warning threshold', required=True)
    required.add_argument('--crit-cpu', help='cpu critical threshold', required=True)
    required.add_argument('--warn-conns', help='free connections warning threshold', required=True)
    required.add_argument('--crit-conns', help='free connections critical threshold', required=True)
    required.add_argument('--warn-disk', help='disk free warning threshold', required=True)
    required.add_argument('--crit-disk', help='disk free critical threshold', required=True)

    parser.add_argument('--instance', help='db instance identifier', required=True)
    parser.add_argument('--last_state', help='use last known value', action='store_true')
    parser.add_argument('--percent', help='compare usage percent instead of absolute numbers'
                                          ' (connections and memory)', action='store_true')
    parser.add_argument('--region', help='AWS region name (default: eu-central-1)',
                        default='eu-central-1')

    args = parser.parse_args()
    states = []

    # gather metrics
    value = unused_connections(args)
    states.append({
        'name': 'free_connections',
        'state': STATE_UNKNOWN if value is None else compare(value, args.warn_conns,
                                                             args.crit_conns),
        'value': value,
        'unit': '%' if args.percent else '',
    })
    value = free_storage(args)
    states.append({
        'name': 'free_storage',
        'state': STATE_UNKNOWN if value is None else compare(
            value, expand_unit(args.warn_disk), expand_unit(args.crit_disk)),
        'value': value if args.percent else value / 1024 / 1024,
        'unit': '%' if args.percent else ' MiB',
    })
    value = cpu_used(args)
    states.append({
        'name': 'cpu_used',
        'state': STATE_UNKNOWN if value is None else compare(value, args.warn_cpu, args.crit_cpu),
        'value': value,
        'unit': '%',
    })

    # determine overall state
    final_state = STATE_OK
    final_text = 'OK:'
    if [item for item in states if item['state'] == STATE_CRIT]:
        final_state = STATE_CRIT
        final_text = 'CRITICAL:'
    elif [item for item in states if item['state'] == STATE_WARN]:
        final_state = STATE_WARN
        final_text = 'WARNING:'
    elif [item for item in states if item['state'] == STATE_UNKNOWN]:
        final_state = STATE_UNKNOWN
        final_text = 'UNKNOWN:'

    print(
        final_text,
        ', '.join(["{}:{}{}".format(item['name'], item['value'], item['unit'])
                   for item in states])
    )
    sys.exit(final_state)


if __name__ == "__main__":
    main()
