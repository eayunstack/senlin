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

import mock
import testtools

from senlin.events import base
from senlin.tests.unit.common import utils

CLUSTER_ID = '2c5139a6-24ba-4a6f-bd53-a268f61536de'


class TestEventBackend(testtools.TestCase):

    def setUp(self):
        super(TestEventBackend, self).setUp()
        self.ctx = utils.dummy_context()

    @mock.patch('oslo_utils.reflection.get_class_name')
    def test__check_entity_cluster(self, mock_get):
        entity = mock.Mock()
        mock_get.return_value = 'Cluster'

        res = base.EventBackend._check_entity(entity)

        self.assertEqual('CLUSTER', res)
        mock_get.assert_called_once_with(entity, fully_qualified=False)

    @mock.patch('oslo_utils.reflection.get_class_name')
    def test__check_entity_node(self, mock_get):
        entity = mock.Mock()
        mock_get.return_value = 'Node'

        res = base.EventBackend._check_entity(entity)

        self.assertEqual('NODE', res)
        mock_get.assert_called_once_with(entity, fully_qualified=False)

    def test__get_action_name(self):
        res = base.EventBackend._get_action_name('FOO_BAR')
        self.assertEqual('bar', res)

    def test__get_action_name_unknown(self):
        res = base.EventBackend._get_action_name('FOOBAR')

        self.assertEqual('unknown', res)

    def test_dump(self):
        self.assertRaises(NotImplementedError,
                          base.EventBackend.dump,
                          '1', '2')
