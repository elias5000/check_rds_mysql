# check_rds_mysql
Icinga/Nagios check to test an AWS RDS MySQL instance against thresholds.


## Required Modules
* boto3


## Installation
    # Checkout source
    git clone https://github.com/elias5000/check_rds_mysql.git
    
    # Install boto3 Python module
    pip install boto3

    # Copy check script
    cp check_rds_mysql.py /usr/lib/nagios/plugins/check_rds_mysql.py
    
    # Copy director config
    cp check_rds_mysql.conf /etc/icinga2/conf.d/check_rds_mysql.conf
    

## Authentication
Authentication is identical to awscli. Use either instance role EC2 or pod role on K8S
with kube2iam (preferred) or ~/.aws/config profile. The check will use the default profile.


## Commandline Usage
    usage: check_rds_mysql.py [-h] --warn-cpu WARN_CPU --crit-cpu CRIT_CPU
                              --warn-conns WARN_CONNS --crit-conns CRIT_CONNS
                              --warn-disk WARN_DISK --crit-disk CRIT_DISK
                              --instance INSTANCE [--last_state] [--percent]
                              [--region REGION]
    
    optional arguments:
      -h, --help            show this help message and exit
      --instance INSTANCE   db instance identifier
      --last_state          use last known value
      --percent             compare usage percent instead of absolute numbers
                            (connections and memory)
      --region REGION       AWS region name (default: eu-central-1)
    
    required arguments:
      --warn-cpu WARN_CPU   cpu warning threshold
      --crit-cpu CRIT_CPU   cpu critical threshold
      --warn-conns WARN_CONNS
                            free connections warning threshold
      --crit-conns CRIT_CONNS
                            free connections critical threshold
      --warn-disk WARN_DISK
                            disk free warning threshold
      --crit-disk CRIT_DISK
                            disk free critical threshold
    
    thresholds and ranges:
      Threshold ranges are in Nagios format:
      https://nagios-plugins.org/doc/guidelines.html#THRESHOLDFORMAT
      For disk threshold you can specify a unit (e.g. "1000Mi:", "8Gi")