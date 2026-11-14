"""Forced-alignment providers (Session V, ElevenLabs).

Wires up ElevenLabs' ``/v1/forced-alignment`` endpoint as a
first-class ``AssetType.ALIGNMENT`` provider, alongside a stub
provider for offline tests.

The provider takes a pre-existing audio file and a known
transcript text, and returns character-level and word-level
timestamps. The output is a JSON sidecar (not a media file)
that can be converted to the ``transcript.json`` shape the
CaptionsSkill already consumes.
"""

from __future__ import annotations
