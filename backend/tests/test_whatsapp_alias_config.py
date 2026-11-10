import pytest
from pydantic import ValidationError

from app.config import Settings, WhatsAppChatAliasGroup


def test_alias_groups_are_explicit_validated_and_account_scoped() -> None:
    group = WhatsAppChatAliasGroup(account_id="a" * 64, pn_jid="5511999999999@s.whatsapp.net", lid_jid="888@lid")
    assert Settings().whatsapp_chat_alias_groups == []
    with pytest.raises(ValidationError, match="disjoint"):
        Settings(whatsapp_chat_alias_groups=[group, group])
    other_account = group.model_copy(update={"account_id": "b" * 64})
    assert len(Settings(whatsapp_chat_alias_groups=[group, other_account]).whatsapp_chat_alias_groups) == 2
    with pytest.raises(ValidationError):
        WhatsAppChatAliasGroup(account_id="a" * 64, pn_jid="123@g.us", lid_jid="888@lid")
    with pytest.raises(ValidationError):
        WhatsAppChatAliasGroup(account_id="not-an-account", pn_jid="123@s.whatsapp.net", lid_jid="888@lid")
