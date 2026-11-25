#!/usr/bin/env python3
"""PS2-era art direction profiles for ``stage3d.json``.

These are lighting, palette, material, and set-dressing recipes for a
cinematic game look (Vice City warmth, industrial olive and sodium vapor,
cold survival fog, tactical steel). Pixelation, CRT scanlines, and texture
jitter stay off unless a caller explicitly opts in. Nothing here is a
photoreal grade and nothing here is a low-resolution filter.
"""
from __future__ import annotations

import copy
from typing import Any

PROFILE_IDS: tuple[str, ...] = (
    "ps2_urban",
    "retro_industrial_scifi",
    "retro_horror",
    "retro_military",
)
ALIASES = {
    "ps2": "ps2_urban",
    "vice_city": "ps2_urban",
    "san_andreas": "ps2_urban",
    "urban": "ps2_urban",
    "industrial": "retro_industrial_scifi",
    "half_life": "retro_industrial_scifi",
    "deus_ex": "retro_industrial_scifi",
    "scifi": "retro_industrial_scifi",
    "horror": "retro_horror",
    "silent_hill": "retro_horror",
    "military": "retro_military",
    "metal_gear": "retro_military",
    "tactical": "retro_military",
}
_TOGGLE_KEYS = ("pixelation", "crt_scanlines", "texture_jitter")

_PROFILES: dict[str, dict[str, Any]] = {
    "ps2_urban": {
        "id": "ps2_urban",
        "palette": {
            "sky": "#ff7a3c",
            "sky_top": "#3a1d5c",
            "horizon": "#ffb15a",
            "neon_magenta": "#ff3d8a",
            "neon_cyan": "#3de0ff",
            "amber": "#ffb020",
            "ocean": "#1a6b7a",
            "asphalt": "#2a2a32",
            "stucco": "#e7c39a",
            "palm_shadow": "#1b3a34",
            "shadow": "#1a1020",
            "key": "#ffc48a",
        },
        "lighting": {
            "ambient": "#c4896a",
            "ambient_energy": 0.48,
            "key_color": "#ffb15a",
            "key_energy": 1.25,
            "key_dir": [-0.55, -0.62, 0.35],
            "fill_color": "#ff7eb6",
            "fill_energy": 0.22,
            "rim_color": "#ffd27a",
            "rim_energy": 0.72,
            "fog_color": "#e39a72",
            "fog_density": 0.012,
            "practicals": [
                {"id": "neon_sign", "color": "#ff3d8a", "energy": 1.6},
                {"id": "sodium_street", "color": "#ffb020", "energy": 1.3},
            ],
        },
        "character_material": {
            "roughness": 0.62,
            "metallic": 0.06,
            "rim_strength": 0.48,
            "wear": 0.22,
            "simplify_shading": True,
            "jacket": "#f2efe6",
            "pants": "#243246",
            "boots": "#1a120e",
            "skin_tint": "#e8b898",
        },
        "environment_dressing": {
            "neon_signs": True,
            "wet_asphalt": True,
            "palm_silhouettes": True,
            "low_traffic_lamps": True,
            "hazard_stripes": False,
            "grime": 0.18,
            "decals": ["neon_arrow", "parking_stencil"],
            "prop_tint": "#c9895a",
        },
        "post_fx": {
            "pixelation": False,
            "crt_scanlines": False,
            "texture_jitter": False,
            "vignette": 0.22,
            "contrast": 1.18,
            "saturation": 1.05,
            "warm_tint": "#ffc48a",
        },
    },
    "retro_industrial_scifi": {
        "id": "retro_industrial_scifi",
        "palette": {
            "sky": "#6d7270",
            "horizon": "#c4a27a",
            "olive": "#6b7048",
            "rust": "#8a4b32",
            "slate": "#3e4650",
            "sodium": "#ffb25a",
            "fluorescent": "#d7e7ea",
            "hazard": "#e2b100",
            "shadow": "#1c2126",
            "key": "#ffd8a8",
        },
        "lighting": {
            "ambient": "#8d8678",
            "ambient_energy": 0.42,
            "key_color": "#ffb25a",
            "key_energy": 1.15,
            "key_dir": [-0.40, -0.70, 0.55],
            "fill_color": "#9eb4b8",
            "fill_energy": 0.28,
            "rim_color": "#ffd8a8",
            "rim_energy": 0.55,
            "fog_color": "#6e6458",
            "fog_density": 0.018,
            "practicals": [
                {"id": "sodium", "color": "#ffb25a", "energy": 1.4},
                {"id": "fluorescent", "color": "#d7e7ea", "energy": 0.7},
            ],
        },
        "character_material": {
            "roughness": 0.72,
            "metallic": 0.08,
            "rim_strength": 0.35,
            "wear": 0.45,
            "simplify_shading": True,
            "jacket": "#4a5340",
            "pants": "#2a2e2c",
            "boots": "#1a1c1a",
            "skin_tint": "#e0b090",
        },
        "environment_dressing": {
            "hazard_stripes": True,
            "heavy_cables": True,
            "exposed_pipes": True,
            "worn_panels": True,
            "worn_machinery": True,
            "grime": 0.35,
            "decals": ["hazard", "stencil_numbers", "oil_stain"],
            "prop_tint": "#6a5a48",
        },
        "post_fx": {
            "pixelation": False,
            "crt_scanlines": False,
            "texture_jitter": False,
            "vignette": 0.25,
            "contrast": 1.14,
            "saturation": 0.92,
            "warm_tint": "#ffd8a8",
        },
    },
    "retro_horror": {
        "id": "retro_horror",
        "palette": {
            "fog": "#8d97a0",
            "sky": "#2a3038",
            "decay": "#5c5348",
            "stain": "#6e4034",
            "flashlight": "#fff4d8",
            "cold_shadow": "#1a1e24",
            "mold": "#3e4638",
            "rust": "#6a4034",
            "key": "#d5dde6",
            "shadow": "#0e1116",
        },
        "lighting": {
            "ambient": "#3a4450",
            "ambient_energy": 0.22,
            "key_color": "#9aabba",
            "key_energy": 0.35,
            "key_dir": [-0.2, -0.8, 0.15],
            "fill_color": "#2c333c",
            "fill_energy": 0.12,
            "rim_color": "#c9d6e2",
            "rim_energy": 0.18,
            "fog_color": "#8d97a0",
            "fog_density": 0.045,
            "flashlight": {"color": "#fff4d8", "energy": 2.4, "angle_deg": 28, "range": 8.0},
            "practicals": [
                {"id": "dying_bulb", "color": "#d8c39a", "energy": 0.35},
            ],
        },
        "character_material": {
            "roughness": 0.84,
            "metallic": 0.02,
            "rim_strength": 0.22,
            "wear": 0.62,
            "simplify_shading": True,
            "jacket": "#4a4038",
            "pants": "#2a2624",
            "boots": "#14110f",
            "skin_tint": "#c9a088",
        },
        "environment_dressing": {
            "cold_fog": True,
            "decayed_grime": True,
            "broken_lights": True,
            "stain_decals": True,
            "hazard_stripes": False,
            "grime": 0.72,
            "decals": ["water_stain", "peeling_paint", "rust_bloom"],
            "prop_tint": "#5c5348",
        },
        "post_fx": {
            "pixelation": False,
            "crt_scanlines": False,
            "texture_jitter": False,
            "vignette": 0.42,
            "contrast": 1.22,
            "saturation": 0.55,
            "warm_tint": "#c9d4dc",
        },
    },
    "retro_military": {
        "id": "retro_military",
        "palette": {
            "steel": "#7d868c",
            "olive": "#4e5640",
            "black": "#14171a",
            "spotlight": "#f4f1e6",
            "dust": "#8a8172",
            "canopy": "#2e332c",
            "hazard": "#9a8a3a",
            "shadow": "#101214",
            "key": "#d9d3c4",
            "sky": "#4d555c",
        },
        "lighting": {
            "ambient": "#3e4448",
            "ambient_energy": 0.28,
            "key_color": "#f4f1e6",
            "key_energy": 1.35,
            "key_dir": [-0.15, -0.92, 0.22],
            "fill_color": "#2a3130",
            "fill_energy": 0.14,
            "rim_color": "#f7f4ea",
            "rim_energy": 0.85,
            "fog_color": "#5c615c",
            "fog_density": 0.02,
            "spotlight": {"color": "#f4f1e6", "energy": 2.1, "angle_deg": 18, "range": 10.0},
            "practicals": [
                {"id": "map_lamp", "color": "#e6d7b0", "energy": 0.6},
            ],
        },
        "character_material": {
            "roughness": 0.7,
            "metallic": 0.12,
            "rim_strength": 0.62,
            "wear": 0.38,
            "simplify_shading": True,
            "jacket": "#4e5640",
            "pants": "#2c3128",
            "boots": "#161814",
            "skin_tint": "#d2a888",
        },
        "environment_dressing": {
            "stencil_marks": True,
            "ammo_crates": True,
            "stark_spotlights": True,
            "desaturated_steel": True,
            "hazard_stripes": True,
            "grime": 0.28,
            "decals": ["stencil_unit", "arrow_black", "crate_id"],
            "prop_tint": "#6d7370",
        },
        "post_fx": {
            "pixelation": False,
            "crt_scanlines": False,
            "texture_jitter": False,
            "vignette": 0.30,
            "contrast": 1.20,
            "saturation": 0.48,
            "warm_tint": "#d9d3c4",
        },
    },
}


class RetroArtProfile:
    """Resolve a named PS2 art profile, or a partial override, into stage JSON."""

    def ids(self) -> list[str]:
        """The four canonical profile ids, in stable order."""
        return list(PROFILE_IDS)

    def export(self, profile_id: str) -> dict[str, Any]:
        """Deep copy of one preset. Pixelation, scanlines, and jitter are off."""
        canonical = self._canonical(profile_id)
        profile = copy.deepcopy(_PROFILES[canonical])
        for key in _TOGGLE_KEYS:
            profile["post_fx"][key] = False
        return profile

    def resolve(self, spec: str | dict[str, Any]) -> dict[str, Any]:
        """Accept a profile name or a dict of overrides.

        Toggle keys stay false unless the dict's ``post_fx`` sets them
        explicitly. A full profile dict is copied rather than discarded.
        """
        if isinstance(spec, str):
            return self.export(spec)
        if not isinstance(spec, dict):
            raise TypeError(f"art_profile must be a name or dict, got {type(spec).__name__}")
        if "palette" in spec and "post_fx" in spec and spec.get("id"):
            profile = copy.deepcopy(spec)
        else:
            profile_id = str(spec.get("id") or spec.get("profile") or "retro_industrial_scifi")
            profile = self.export(profile_id)
            for section in ("palette", "lighting", "character_material", "environment_dressing", "post_fx"):
                overlay = spec.get(section)
                if isinstance(overlay, dict):
                    profile[section].update(copy.deepcopy(overlay))
        explicit = spec.get("post_fx") if isinstance(spec.get("post_fx"), dict) else {}
        profile.setdefault("post_fx", {})
        for key in _TOGGLE_KEYS:
            if key in explicit:
                profile["post_fx"][key] = bool(explicit[key])
            else:
                profile["post_fx"][key] = False
        if "id" not in profile:
            profile["id"] = str(spec.get("id") or spec.get("profile") or "retro_industrial_scifi")
        if "modular_scene_assets" in spec:
            profile["modular_scene_assets"] = bool(spec["modular_scene_assets"])
        return profile

    def _canonical(self, profile_id: str) -> str:
        key = str(profile_id or "").strip().lower()
        key = ALIASES.get(key, key)
        if key not in _PROFILES:
            known = ", ".join(PROFILE_IDS)
            raise KeyError(f"unknown art profile {profile_id!r}; expected one of {known}")
        return key
