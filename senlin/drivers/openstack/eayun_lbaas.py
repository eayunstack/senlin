# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import eventlet
import six

from oslo_context import context as oslo_context
from oslo_log import log as logging

from senlin.common import exception
from senlin.common.i18n import _
from senlin.common.i18n import _LE
from senlin.drivers import base
from senlin.drivers.openstack import neutron_v2 as neutronclient

LOG = logging.getLogger(__name__)


class LoadBalancerDriver(base.DriverBase):
    """Load-balancing driver based on Neutron LBaaS V2 service."""

    def __init__(self, params):
        super(LoadBalancerDriver, self).__init__(params)
        self.lb_status_timeout = 600
        self._nc = None

    def nc(self):
        if self._nc:
            return self._nc

        self._nc = neutronclient.NeutronClient(self.conn_params)
        return self._nc

    def lb_get(self, name_or_id):
        """Create a LBaaS instance

        :param pool: A dict describing the pool of load-balancer members.
        """

        result = {}
        # Get has exist loadbance pool
        try:
            pool = self.nc().pool_get_v1(name_or_id)
        except exception.InternalError as ex:
            msg = _LE('Failed in getting pool: %s.') % six.text_type(ex)
            LOG.exception(msg)
            return False, msg

        if pool.status != 'ACTIVE':
            LOG.error(_LE('The loadbacne pool: %s is not active.' % pool.id))
            return False, result

        result['pool_id'] = pool.id
        result['vip_address'] = pool.vip_id
        result['health_monitors'] = pool.health_monitors
        result['subnet_id'] = pool.subnet_id

        return True, result

    def lb_delete(self, **kwargs):
        """Delete a Neutron lbaas instance

        The policy add has exist loadbance to cluster,
        Delete don't do anything
        """

        return True, _('LB deletion succeeded')

    def member_add(self, node, pool_id, subnet_id, port):
        """Add a member to Neutron lbaas pool.

        :param node: A node object to be added to the specified pool.
        :param pool_id: The ID of the pool for receiving the node.
        :param subnet_id: The subnet to be used by the new LB member.
        :param port: The port for the new LB member to be created.
        :returns: The ID of the new LB member or None if errors occurred.
        """
        try:
            subnet_obj = self.nc().subnet_get(subnet_id)
            net_id = subnet_obj.network_id
            net = self.nc().network_get(net_id)
        except exception.InternalError as ex:
            resource = 'subnet' if subnet_id in ex.message else 'network'
            msg = _LE('Failed in getting %(resource)s: %(msg)s.'
                      ) % {'resource': resource, 'msg': six.text_type(ex)}
            LOG.exception(msg)
            return None
        net_name = net.name

        node_detail = node.get_details(oslo_context.get_current())
        addresses = node_detail.get('addresses')
        if net_name not in addresses:
            msg = _LE('%(name)s address not in subnet %(subnet)s')
            LOG.error(msg, {'name': node_detail.get('name'),
                            'subnet': subnet_id})
            return None

        # Use the first IP address if more than one are found in target network
        address = addresses[net_name][0]['addr']
        try:
            # FIXME(Yanyan Hu): Currently, Neutron lbaasv2 service can not
            # handle concurrent lb member operations well: new member creation
            # deletion request will directly fail rather than being lined up
            # when another operation is still in progress. In this workaround,
            # loadbalancer status will be checked before creating lb member
            # request is sent out. If loadbalancer keeps unready till waiting
            # timeout, exception will be raised to fail member_add.
            member = self.nc().pool_member_create_v1(pool_id, address, port)
        except (exception.InternalError, exception.Error) as ex:
            msg = _LE('Failed in creating lb pool member: %s.'
                      ) % six.text_type(ex)
            LOG.exception(msg)
            return None

        return member.id

    def member_remove(self, member_id):
        """Delete a member from Neutron lbaas pool.

        :param lb_id: The ID of the loadbalancer the operation is targeted at;
        :param pool_id: The ID of the pool from which the member is deleted;
        :param member_id: The ID of the LB member.
        :returns: True if the operation succeeded or False if errors occurred.
        """
        try:
            # FIXME(Yanyan Hu): Currently, Neutron lbaasv2 service can not
            # handle concurrent lb member operations well: new member creation
            # deletion request will directly fail rather than being lined up
            # when another operation is still in progress. In this workaround,
            # loadbalancer status will be checked before deleting lb member
            # request is sent out. If loadbalancer keeps unready till waiting
            # timeout, exception will be raised to fail member_remove.
            self.nc().pool_member_delete_v1(member_id)
        except (exception.InternalError, exception.Error) as ex:
            msg = _LE('Failed in removing member %(m)s: '
                      '%(ex)s') % {'m': member_id,
                                   'ex': six.text_type(ex)}
            LOG.exception(msg)
            return None

        return True
