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

from senlin.drivers import base
from senlin.drivers.openstack import sdk


class CinderClient(base.DriverBase):
    """Cinder V2 driver."""

    def __init__(self, params):
        super(CinderClient, self).__init__(params)
        self.conn = sdk.create_connection(params)
        self.session = self.conn.session

    @sdk.translate_exception
    def create_volume(self, **attr):
        res = self.conn.block_store.volume_create(**attr)
        return res

    @sdk.translate_exception
    def delete_volume(self, volume_id, ignore_missing=True):
        res = self.conn.block_store.delete_volume(
            volume_id, ignore_missing=ignore_missing)
        return res

    @sdk.translate_exception
    def volumes(self, details=True, **attr):
        res = self.conn.block_store.volumes(details=details, **attr)
        return res

    @sdk.translate_exception
    def get_volume(self, volume):
        res = self.conn.block_store.get_volume(volume)
        return res
