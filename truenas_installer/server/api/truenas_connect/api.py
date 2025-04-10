import asyncio
import errno
import time
import uuid
from urllib.parse import urlencode

from truenas_connect_utils.install_schema import TNC_CONFIG_SCHEMA
from truenas_connect_utils.urls import get_registration_uri

from truenas_installer.server.error import Error
from truenas_installer.server.method import method

from .cache import get_tnc_config, update_tnc_config
from .registration import finalize_registration


__all__ = ['tnc_config', 'configure_tnc', 'tnc_registration_uri']


@method(None, TNC_CONFIG_SCHEMA)
async def tnc_config(context):
    """
    Get TrueNAS Connect configuration.
    """
    return get_tnc_config()


@method({
    'type': 'object',
    'properties': {
        'ips': {
            'type': 'array',
            'items': {
                'type': 'string',  # FIXME: Validate this to be proper IP Addresses
            },
        },
        'enabled': {'type': 'boolean'},
        'account_service_base_url': {'type': 'string'},
        'leca_service_base_url': {'type': 'string'},
        'tnc_base_url': {'type': 'string'},
    },
    'required': ['enabled', 'ips'],
}, TNC_CONFIG_SCHEMA)
async def configure_tnc(context, data):
    """
    Enable and configure TrueNAS Connect.
    """
    if context.server.configured_tnc is True:
        raise Error('Configuration can only be updated once', errno.EINVAL)

    if data['enabled'] and not data['ips']:
        raise Error('No IP addresses provided', errno.EINVAL)

    if data['enabled']:
        context.server.configured_tnc = True

    config = update_tnc_config(data)
    if config['enabled']:
        # Let's generate claim token
        update_tnc_config({
            'claim_token': str(uuid.uuid4()),
            'claim_token_expiration': time.time() + (45 * 60),  # 45 min expiration
            'system_id': str(uuid.uuid4()),
            'truenas_version': context.server.installer.version,
            'initialization_in_progress': True,
        })
        asyncio.get_event_loop().create_task(finalize_registration())

    return get_tnc_config()


@method(None, {'type': 'string'})
async def tnc_registration_uri(context):
    config = get_tnc_config()
    if config['initialization_in_progress'] is False:
        raise Error('TrueNAS Connect needs to be enabled first', errno.EINVAL)

    query_params = {
        'version': config['truenas_version'],
        'model': 'UNKNOWN',  # FIXME: Obviously fix this
        'system_id': config['system_id'],
        'token': config['claim_token'],
    }

    return f'{get_registration_uri(config)}?{urlencode(query_params)}'
