import pytest
import re


from core.cloner_engine import apply_text_transformations, is_media_allowed as is_media_type_allowed


# Tests
def test_remove_captions():
    text = "🔥 Legenda em outro idioma / anúncio promocional"
    transformed = apply_text_transformations(text, remove_captions=True)
    assert transformed is None

    transformed_with_header = apply_text_transformations(
        text,
        remove_captions=True,
        header_text="Meu cabeçalho limpo"
    )
    assert transformed_with_header == "Meu cabeçalho limpo"
    assert "Legenda em outro idioma" not in transformed_with_header
def test_remove_links():
    text = "Confira a promoção em https://promocao.com/oferta ou t.me/canaloriginal agora!"
    transformed = apply_text_transformations(text, remove_links=True)
    assert "https://" not in transformed
    assert "t.me/" not in transformed
    assert "Confira a promoção em" in transformed


def test_remove_mentions():
    text = "Siga nosso admin @canal_orig e o parceiro @parceiro123 para mais novidades."
    transformed = apply_text_transformations(text, remove_mentions=True)
    assert "@canal_orig" not in transformed
    assert "@parceiro123" not in transformed


def test_custom_replacements_exact_and_regex():
    text = "Cupom de desconto: DESCONTO10 no link http://meulink.com"
    replacements = [
        {"pattern": "DESCONTO10", "replacement": "VIP20", "is_regex": False, "enabled": True},
        {"pattern": r"http://meulink\.com", "replacement": "https://novosite.com", "is_regex": True, "enabled": True},
        {"pattern": "NaoUsar", "replacement": "X", "is_regex": False, "enabled": False}
    ]
    transformed = apply_text_transformations(text, custom_replacements=replacements)
    assert "VIP20" in transformed
    assert "https://novosite.com" in transformed


def test_header_and_footer():
    text = "Conteúdo exclusivo da postagem."
    transformed = apply_text_transformations(
        text,
        header_text="🔥 [MEU CANAL VIP]",
        footer_text="👉 Assine: https://t.me/meucanal"
    )
    assert transformed.startswith("🔥 [MEU CANAL VIP]\n\nConteúdo exclusivo")
    assert transformed.endswith("👉 Assine: https://t.me/meucanal")


def test_media_type_filtering():
    assert is_media_type_allowed("photo", ["all"]) is True
    assert is_media_type_allowed("video", ["photo", "video"]) is True
    assert is_media_type_allowed("sticker", ["photo", "video"]) is False


def test_is_forwards_restricted_detection():
    from core.cloner_engine import _is_forwards_restricted
    from types import SimpleNamespace

    # RPCError with standard string
    err1 = Exception("Telegram says: [400 CHAT_FORWARDS_RESTRICTED] - The chat restricts forwarding content")
    assert _is_forwards_restricted(err1) is True

    # Error containing message substring
    err2 = Exception("The chat restricts forwarding content (caused by 'messages.SendMedia')")
    assert _is_forwards_restricted(err2) is True

    # Other errors
    err3 = Exception("Telegram says: [400 PEER_ID_INVALID]")
    assert _is_forwards_restricted(err3) is False


def test_get_media_file_size():
    from core.cloner_engine import _get_media_file_size
    from types import SimpleNamespace

    # Mock photo message
    msg_photo = SimpleNamespace(
        photo=SimpleNamespace(file_size=1024 * 500),
        video=None, document=None, audio=None, voice=None,
        animation=None, sticker=None, video_note=None
    )
    assert _get_media_file_size(msg_photo) == 512000

    # Mock video message
    msg_video = SimpleNamespace(
        photo=None,
        video=SimpleNamespace(file_size=1024 * 1024 * 50),
        document=None, audio=None, voice=None,
        animation=None, sticker=None, video_note=None
    )
    assert _get_media_file_size(msg_video) == 52428800

    # Empty message
    assert _get_media_file_size(None) is None


@pytest.mark.asyncio
async def test_interruptible_sleep():
    import asyncio
    from core.cloner_engine import _interruptible_sleep, TaskPausedDuringWait

    # 1. Normal short sleep finishes
    finished = await _interruptible_sleep(0.05)
    assert finished is True

    # 2. Interrupted by pause_event
    pause_evt = asyncio.Event()
    pause_evt.clear()  # paused
    with pytest.raises(TaskPausedDuringWait):
        await _interruptible_sleep(10.0, pause_event=pause_evt)

    # 3. Interrupted by cancel_event
    cancel_evt = asyncio.Event()
    cancel_evt.set()  # cancelled
    with pytest.raises(asyncio.CancelledError):
        await _interruptible_sleep(10.0, cancel_event=cancel_evt)
