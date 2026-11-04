"""Concrete, unique allowlisted Windows settings; no arbitrary paths from journals."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryRule:
    path: str
    name: str
    target: int
    scope: str = "HKCU"
    min_build: int = 17763
    max_build: int = 999999
    edition: str = "any"  # any / pro / enterprise
    app: str = ""


EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
EXPLORER_POLICY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
CLOUD = r"Software\Policies\Microsoft\Windows\CloudContent"
SEARCH = r"Software\Policies\Microsoft\Windows\Windows Search"
REGISTRY = {
    "game_mode": RegistryRule(r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1),
    "capture": RegistryRule(r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "HistoricalCaptureEnabled", 0),
    "controller_bar": RegistryRule(r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", 0),
    "transparency": RegistryRule(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0),
    "edge_startup": RegistryRule(r"Software\Policies\Microsoft\Edge", "StartupBoostEnabled", 0, app="edge"),
    "edge_background": RegistryRule(r"Software\Policies\Microsoft\Edge", "BackgroundModeEnabled", 0, app="edge"),
    "widgets": RegistryRule(r"Software\Policies\Microsoft\Dsh", "AllowNewsAndInterests", 0, "HKLM", 22000, edition="pro"),
    "feeds": RegistryRule(r"Software\Policies\Microsoft\Windows\Windows Feeds", "EnableFeeds", 0, "HKLM", 19041, 21999, "pro"),
    "uwp_background": RegistryRule(r"Software\Policies\Microsoft\Windows\AppPrivacy", "LetAppsRunInBackground", 2, "HKLM", edition="pro"),
    "cloud_search": RegistryRule(SEARCH, "AllowCloudSearch", 0, "HKLM", edition="pro"),
    "search_highlights": RegistryRule(SEARCH, "EnableDynamicContentInWSB", 0, "HKLM", 19044, edition="pro"),
    "toast_notifications": RegistryRule(r"Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications", "NoToastApplicationNotification", 1, edition="pro"),
    "tailored": RegistryRule(CLOUD, "DisableTailoredExperiencesWithDiagnosticData", 1, edition="pro"),
    "spotlight_settings": RegistryRule(CLOUD, "DisableWindowsSpotlightOnSettings", 1, edition="enterprise"),
    "spotlight_notifications": RegistryRule(CLOUD, "DisableWindowsSpotlightOnActionCenter", 1, edition="enterprise"),
    "welcome": RegistryRule(CLOUD, "DisableWindowsSpotlightWindowsWelcomeExperience", 1, edition="enterprise"),
    "explorer_search_history": RegistryRule(r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", 1, edition="pro"),
    "recent_documents": RegistryRule(EXPLORER_POLICY, "NoRecentDocsHistory", 1, edition="pro"),
    "app_tracking": RegistryRule(EXPLORER_POLICY, "NoInstrumentation", 1, edition="pro"),
    "icons_only": RegistryRule(EXPLORER, "IconsOnly", 1),
    "preview_handlers": RegistryRule(EXPLORER, "ShowPreviewHandlers", 0),
    "folder_tips": RegistryRule(EXPLORER, "ShowInfoTip", 0),
}


@dataclass(frozen=True)
class SpiRule:
    get: int
    set: int
    kind: str
    target: int = 0
    minimum: int = 0
    maximum: int = 1


SPI = {
    "animations": SpiRule(0x1042, 0x1043, "bool_pv"),
    "menu_animation": SpiRule(0x1002, 0x1003, "bool_pv"),
    "menu_fade": SpiRule(0x1012, 0x1013, "bool_pv"),
    "combo_animation": SpiRule(0x1004, 0x1005, "bool_pv"),
    "list_smooth": SpiRule(0x1006, 0x1007, "bool_pv"),
    "selection_fade": SpiRule(0x1014, 0x1015, "bool_pv"),
    "tooltip_animation": SpiRule(0x1016, 0x1017, "bool_pv"),
    "tooltip_fade": SpiRule(0x1018, 0x1019, "bool_pv"),
    "cursor_shadow": SpiRule(0x101A, 0x101B, "bool_pv"),
    "drop_shadow": SpiRule(0x1024, 0x1025, "bool_pv"),
    "gradient_captions": SpiRule(0x1008, 0x1009, "bool_pv"),
    "hot_tracking": SpiRule(0x100E, 0x100F, "bool_pv"),
    "drag_full_windows": SpiRule(0x0026, 0x0025, "bool_ui"),
    "window_animation": SpiRule(0x0048, 0x0049, "animation"),
    "menu_delay": SpiRule(0x006A, 0x006B, "int_ui", 100, 0, 10000),
    "mouse_trails": SpiRule(0x005E, 0x005D, "int_ui", 0, 0, 0xFFFFFFFF),
    "mouse_sonar": SpiRule(0x101C, 0x101D, "bool_pv"),
    "mouse_snap": SpiRule(0x005F, 0x0060, "bool_ui"),
    "mouse_clicklock": SpiRule(0x101E, 0x101F, "bool_pv"),
    "keyboard_delay": SpiRule(0x0016, 0x0017, "int_ui", 0, 0, 3),
    "keyboard_speed": SpiRule(0x000A, 0x000B, "int_ui", 31, 0, 31),
    "sticky_hotkey": SpiRule(0x003A, 0x003B, "sticky"),
    "filter_hotkey": SpiRule(0x0032, 0x0033, "filter"),
    "toggle_hotkey": SpiRule(0x0034, 0x0035, "toggle"),
}


@dataclass(frozen=True)
class PowerRule:
    subgroup: str
    setting: str
    target: int


CPU = "54533251-82be-4824-96c1-47b60b740d00"
POWER = {
    "cpu_max": PowerRule(CPU, "bc5038f7-23e0-4960-96da-33abaf5935ec", 100),
    "cpu_epp": PowerRule(CPU, "36687f9e-e3a5-4dbf-b1dc-15eb381c6863", 0),
    "cpu_boost": PowerRule(CPU, "be337238-0d82-4146-a960-4f3749d470c7", 2),
    "pcie_aspm": PowerRule("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0),
    "disk_timeout": PowerRule("0012ee47-9041-4b5d-9b77-535fba8b1442", "6738e2c4-e8a5-4a42-b16a-e040e769756e", 0),
    "wifi_power": PowerRule("19cbb8fa-5279-450e-9fac-8a3d5fedd0c1", "12bbebe6-58d6-4636-95bb-3217ef867c1a", 0),
    "sleep_timeout": PowerRule("238c9fa8-0aad-41ed-83f4-97be242c8f20", "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 0),
    "display_timeout": PowerRule("7516b95f-f776-4464-8c53-06167f40cc99", "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", 0),
}

ALL_IDS = frozenset(REGISTRY) | frozenset(SPI) | frozenset(POWER) | {"power", "mouse"}
