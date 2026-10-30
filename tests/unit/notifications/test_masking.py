"""租户渠道配置回显验证（webhook 脱敏、邮箱完整回显）。"""
from src.notifications.repository import mask_tenant_configuration, protected, public_target


class TestMaskTenantConfiguration:
    def test_im_webhook_masked(self):
        masked = mask_tenant_configuration(
            'im_webhook', {'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/3f2a-9x8y'})
        assert masked == 'https://open.feishu.cn/****9x8y'

    def test_wecom_query_key_masked(self):
        masked = mask_tenant_configuration(
            'im_webhook', {'webhook_url': 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc12345'})
        assert masked == 'https://qyapi.weixin.qq.com/****2345'

    def test_email_full_display(self):
        assert mask_tenant_configuration('email', {'email_address': 'alice@example.com'}) == 'alice@example.com'

    def test_invalid_inputs(self):
        assert mask_tenant_configuration('im_webhook', {'webhook_url': 'not-a-url'}) == '****'
        assert mask_tenant_configuration('email', {'email_address': ''}) is None
        assert mask_tenant_configuration('web_push', {'endpoint': 'https://x'}) is None
        assert mask_tenant_configuration('im_webhook', None) is None

    def test_public_target_includes_display(self):
        row = {'target_id': 't', 'installation_ref': 'i', 'binding_version': 1, 'state': 'active',
               'enabled_since': None, 'channel': 'im_webhook', 'provider': 'feishu_bot',
               'rule_id': None,
               'configuration_encrypted': protected({'webhook_url': 'https://open.feishu.cn/hook/abcd1234'})}
        assert public_target(row)['configuration_display'] == 'https://open.feishu.cn/****1234'

    def test_public_target_web_push_no_display(self):
        row = {'target_id': 't', 'installation_ref': 'i', 'binding_version': 1, 'state': 'active',
               'enabled_since': None, 'channel': 'web_push', 'provider': 'web_push',
               'rule_id': None,
               'configuration_encrypted': protected({'endpoint': 'https://push.example.com'})}
        assert 'configuration_display' not in public_target(row)
