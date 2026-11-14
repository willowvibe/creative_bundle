"""ElevenLabs is used for voice-over only (user rule, 2026-10-30).

Its text-to-speech (and the voice cloning that feeds it) is the default narration path. Music, sound effects,
forced alignment, voice isolation and speech-to-speech must come from other providers, so the five non-voice
ElevenLabs providers call :func:`ensure_voice_only` before every request, and the asset manager checks the
provider name before building one. A direct construction therefore cannot bypass the rule either.
"""

from __future__ import annotations

import os

VOICE_PROVIDERS = frozenset({"elevenlabs_tts", "elevenlabs_clone"})

#: What to use instead, quoted in the error.
ALTERNATIVES = {
    "elevenlabs_music": "poyo_music or ace_step",
    "elevenlabs_sfx": "earshot_sfx, freesound_sfx or openverse_sfx",
    "elevenlabs_forced_alignment": "a whisper transcript (no non-ElevenLabs aligner is wired yet)",
    "elevenlabs_isolation": "no replacement is wired yet",
    "elevenlabs_speech_to_speech": "no replacement is wired yet",
}

#: Escape hatch for the providers' own offline unit tests only; never set it in a real environment.
TEST_OVERRIDE_ENV = "VE_TEST_ALLOW_ELEVENLABS_NON_VOICE"


class ElevenLabsVoiceOnlyError(RuntimeError):
    """A non-voice ElevenLabs provider was asked to run."""


def ensure_voice_only(provider_name: str) -> None:
    """Raise unless ``provider_name`` is not ElevenLabs, or is an ElevenLabs voice-over provider."""
    if not provider_name.startswith("elevenlabs") or provider_name in VOICE_PROVIDERS:
        return
    if os.getenv(TEST_OVERRIDE_ENV) == "1":
        return
    hint = ALTERNATIVES.get(provider_name, "a non-ElevenLabs provider")
    raise ElevenLabsVoiceOnlyError(
        f"{provider_name} is disabled: ElevenLabs is used for voice-over only. Use {hint} instead."
    )
