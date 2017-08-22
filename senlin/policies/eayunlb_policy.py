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

"""
Policy for load-balancing among nodes in a cluster.

NOTE: For full documentation about how the deletion policy works, check:
http://docs.openstack.org/developer/senlin/developer/policies/
load_balance_v1.html
"""

from oslo_context import context as oslo_context
from oslo_log import log as logging

from senlin.common import constraints
from senlin.common import consts
from senlin.common import exception as exc
from senlin.common.i18n import _, _LW
from senlin.common import scaleutils
from senlin.common import schema
from senlin.engine import cluster_policy
from senlin.engine import node as nm
from senlin.objects import cluster as co
from senlin.objects import node as no
from senlin.policies import base

LOG = logging.getLogger(__name__)


class LoadBalancingPolicy(base.Policy):
    """Policy for load balancing among members of a cluster.

    This policy is expected to be enforced before or after the membership of a
    cluster is changed. We need to refresh the load-balancer associated with
    the cluster (which could be created by the policy) when these actions are
    performed.
    """
    VERSION = '1.1'

    PRIORITY = 500

    TARGET = [
        ('AFTER', consts.CLUSTER_ADD_NODES),
        ('AFTER', consts.CLUSTER_SCALE_OUT),
        ('AFTER', consts.CLUSTER_RESIZE),
        ('AFTER', consts.NODE_CREATE),
        ('BEFORE', consts.CLUSTER_DEL_NODES),
        ('BEFORE', consts.CLUSTER_SCALE_IN),
        ('BEFORE', consts.CLUSTER_RESIZE),
        ('BEFORE', consts.NODE_DELETE),
    ]

    PROFILE_TYPE = [
        'os.nova.server-1.0',
    ]

    KEYS = (
        POOL, LB_STATUS_TIMEOUT
    ) = (
        'pool', 'lb_status_timeout'
    )

    _POOL_KEYS = (
        POOL_ID, POOL_PROTOCOL_PORT
    ) = (
        'pool_id', 'protocol_port'
    )
    properties_schema = {
        POOL: schema.Map(
            _('LB pool properties.'),
            schema={
                POOL_ID: schema.String(
                    _('Has exist pool id.'),
                    required=True
                ),
                POOL_PROTOCOL_PORT: schema.Integer(
                    _('Port on which servers are running on the nodes.'),
                    default=80,
                ),
            },
        ),
        LB_STATUS_TIMEOUT: schema.Integer(
            _('Time in second to wait for loadbalancer to be ready'
              '(provisioning_status is ACTIVE and operating_status is '
              'ONLINE) before and after senlin requests lbaas V1 service '
              'for lb operations. '),
            default=600,
        )
    }

    def __init__(self, name, spec, **kwargs):
        super(LoadBalancingPolicy, self).__init__(name, spec, **kwargs)

        self.pool_spec = self.properties.get(self.POOL, {})
        self.lb_status_timeout = self.properties.get(self.LB_STATUS_TIMEOUT)
        self.lb = None

    def validate(self, context, validate_props=False):
        super(LoadBalancingPolicy, self).validate(context, validate_props)

        if not validate_props:
            return True

        nc = self.network(context.user, context.project)

        # validate pool subnet
        name_or_id = self.pool_spec.get(self.POOL_ID)
        if name_or_id:
            try:
                nc.pool_get_v1(name_or_id)
            except exc.InternalError:
                msg = _("The specified %(key)s '%(value)s' could not be found."
                        ) % {'key': self.POOL_ID, 'value': name_or_id}
                raise exc.InvalidSpec(message=msg)

    def attach(self, cluster):
        """Routine to be invoked when policy is to be attached to a cluster.

        :param cluster: The target cluster to be attached to;
        :returns: When the operation was successful, returns a tuple (True,
                  message); otherwise, return a tuple (False, error).
        """
        res, data = super(LoadBalancingPolicy, self).attach(cluster)
        if res is False:
            return False, data

        nodes = nm.Node.load_all(oslo_context.get_current(),
                                 cluster_id=cluster.id)

        lb_driver = self.eayunlbaas(cluster.user, cluster.project)
        lb_driver.lb_status_timeout = self.lb_status_timeout

        name_or_id = self.pool_spec.get(self.POOL_ID)
        res, data = lb_driver.lb_get(name_or_id)
        if res is False:
            return False, data

        port = self.pool_spec.get(self.POOL_PROTOCOL_PORT)
        for node in nodes:
            member_id = lb_driver.member_add(node, data['pool_id'],
                                             data['subnet_id'], port)
            if member_id is None:
                # When failed in adding member, remove all lb resources that
                # were created and return the failure reason.
                # TODO(anyone): May need to "roll-back" changes caused by any
                # successful member_add() calls.
                lb_driver.lb_delete(**data)
                return False, ("The cluster node %s don't add in to %s pool"
                               % (node.name, data['pool_id']))

            node.data.update({'lb_member': member_id})
            node.store(oslo_context.get_current())

        cluster_data_lb = cluster.data.get('loadbalancers', {})
        cluster_data_lb[self.id] = {'vip_address': data.pop('vip_address'),
                                    'pool_id': name_or_id}
        cluster.data['loadbalancers'] = cluster_data_lb

        policy_data = self._build_policy_data(data)

        return True, policy_data

    def detach(self, cluster):
        """Routine to be called when the policy is detached from a cluster.

        :param cluster: The cluster from which the policy is to be detached.
        :returns: When the operation was successful, returns a tuple of
            (True, data) where the data contains references to the resources
            created; otherwise returns a tuple of (False, err) where the err
            contains a error message.
        """
        reason = _('LB resources deletion succeeded.')
        lb_driver = self.eayunlbaas(cluster.user, cluster.project)
        lb_driver.lb_status_timeout = self.lb_status_timeout

        cp = cluster_policy.ClusterPolicy.load(oslo_context.get_current(),
                                               cluster.id, self.id)

        policy_data = self._extract_policy_data(cp.data)
        if policy_data is None:
            return True, reason

        res, reason = lb_driver.lb_delete(**policy_data)
        if res is False:
            return False, reason

        nodes = nm.Node.load_all(oslo_context.get_current(),
                                 cluster_id=cluster.id)
        for node in nodes:
            if 'lb_member' in node.data:
                member_id = node.data.get('lb_member')
                # The policy detach, must be remove member from pool
                res = lb_driver.member_remove(member_id)
                if res is False:
                    return False
                node.data.pop('lb_member')
                node.store(oslo_context.get_current())

        lb_data = cluster.data.get('loadbalancers', {})
        if lb_data and isinstance(lb_data, dict):
            lb_data.pop(self.id, None)
            if lb_data:
                cluster.data['loadbalancers'] = lb_data
            else:
                cluster.data.pop('loadbalancers')

        return True, reason

    def _get_delete_candidates(self, cluster_id, action):
        deletion = action.data.get('deletion', None)
        # No deletion field in action.data which means no scaling
        # policy or deletion policy is attached.
        candidates = None
        if deletion is None:
            if action.action == consts.NODE_DELETE:
                candidates = [action.node.id]
                count = 1
            elif action.action == consts.CLUSTER_DEL_NODES:
                # Get candidates from action.input
                candidates = action.inputs.get('candidates', [])
                count = len(candidates)
            elif action.action == consts.CLUSTER_RESIZE:
                # Calculate deletion count based on action input
                db_cluster = co.Cluster.get(action.context, cluster_id)
                scaleutils.parse_resize_params(action, db_cluster)
                if 'deletion' not in action.data:
                    return []
                else:
                    count = action.data['deletion']['count']
            else:  # action.action == consts.CLUSTER_SCALE_IN
                count = 1
        else:
            count = deletion.get('count', 0)
            candidates = deletion.get('candidates', None)

        # Still no candidates available, pick count of nodes randomly
        if candidates is None:
            if count == 0:
                return []
            nodes = no.Node.get_all_by_cluster(action.context, cluster_id)
            if count > len(nodes):
                count = len(nodes)
            candidates = scaleutils.nodes_by_random(nodes, count)
            deletion_data = action.data.get('deletion', {})
            deletion_data.update({
                'count': len(candidates),
                'candidates': candidates
            })
            action.data.update({'deletion': deletion_data})

        return candidates

    def pre_op(self, cluster_id, action):
        """Routine to be called before an action has been executed.

        For this particular policy, we take this chance to update the pool
        maintained by the load-balancer.

        :param cluster_id: The ID of the cluster on which a relevant action
            has been executed.
        :param action: The action object that triggered this operation.
        :returns: Nothing.
        """

        candidates = self._get_delete_candidates(cluster_id, action)
        if len(candidates) == 0:
            return

        db_cluster = co.Cluster.get(action.context, cluster_id)
        lb_driver = self.eayunlbaas(db_cluster.user, db_cluster.project)
        lb_driver.lb_status_timeout = self.lb_status_timeout
        cp = cluster_policy.ClusterPolicy.load(action.context, cluster_id,
                                               self.id)
        policy_data = self._extract_policy_data(cp.data)
        pool_id = policy_data['pool_id']

        # Remove nodes that will be deleted from lb pool
        for node_id in candidates:
            node = nm.Node.load(action.context, node_id=node_id)
            member_id = node.data.get('lb_member', None)
            if member_id is None:
                LOG.warning(_LW('Node %(n)s not found in lb pool %(p)s.'),
                            {'n': node_id, 'p': pool_id})
                continue

            res = lb_driver.member_remove(member_id)
            if res is not True:
                action.data['status'] = base.CHECK_ERROR
                action.data['reason'] = _('Failed in removing deleted '
                                          'node(s) from lb pool.')
                return
            node.data.pop('lb_member')
            node.store(action.context)

        return

    def post_op(self, cluster_id, action):
        """Routine to be called after an action has been executed.

        For this particular policy, we take this chance to update the pool
        maintained by the load-balancer.

        :param cluster_id: The ID of the cluster on which a relevant action
            has been executed.
        :param action: The action object that triggered this operation.
        :returns: Nothing.
        """
        # TODO(Yanyanhu): Need special handling for cross-az scenario
        # which is supported by Neutron eayunlbaas.
        if action.action == consts.NODE_CREATE:
            nodes_added = [action.node.id]
        else:
            creation = action.data.get('creation', None)
            nodes_added = creation.get('nodes', []) if creation else []
            if len(nodes_added) == 0:
                return

        db_cluster = co.Cluster.get(action.context, cluster_id)
        lb_driver = self.eayunlbaas(db_cluster.user, db_cluster.project)
        lb_driver.lb_status_timeout = self.lb_status_timeout
        cp = cluster_policy.ClusterPolicy.load(action.context, cluster_id,
                                               self.id)
        policy_data = self._extract_policy_data(cp.data)
        pool_id = policy_data['pool_id']
        subnet_id = policy_data['subnet_id']
        port = self.pool_spec.get(self.POOL_PROTOCOL_PORT)

        # Add new nodes to lb pool
        for node_id in nodes_added:
            node = nm.Node.load(action.context, node_id=node_id)
            member_id = node.data.get('lb_member', None)
            if member_id:
                LOG.warning(_LW('Node %(n)s already in lb pool %(p)s.'),
                            {'n': node_id, 'p': pool_id})
                continue

            member_id = lb_driver.member_add(node, pool_id, subnet_id,
                                             port)
            if member_id is None:
                action.data['status'] = base.CHECK_ERROR
                action.data['reason'] = _('Failed in adding new node(s) '
                                          'into lb pool.')
                return

            node.data.update({'lb_member': member_id})
            node.store(action.context)

        return
