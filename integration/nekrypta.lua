-- Installed by NEKRYPTA. Existing wallpaper, polkit and clipboard services remain owned by your config.
-- The shortcuts are the ones fixed in core/personal.json; the installer renders this table from there.
local command = os.getenv("HOME") .. "/.local/bin/nekrypta "
local shortcuts = {
    ["SUPER + SPACE"] = "launcher", ["SUPER + R"] = "launcher", ["SUPER + V"] = "clipboard",
    ["SUPER + A"] = "control", ["SUPER + N"] = "notifications",
    ["CTRL + SHIFT + Escape"] = "system",
    ["SUPER + M"] = "power", ["Print"] = "screenshot region",
    ["SUPER + L"] = "lock",
    ["SHIFT + Print"] = "screenshot output", ["SUPER + Print"] = "capture",
}
for keys, action in pairs(shortcuts) do
    -- One bad entry must not cost the other shortcuts.
    pcall(hl.bind, keys, hl.dsp.exec_cmd(command .. action), { description = "NEKRYPTA: " .. action })
end
-- Lets hyprlock take over if the shell's lock screen stops while the session is locked.
hl.config({ misc = { allow_session_lock_restore = true } })
hl.layer_rule({ name = "nekrypta-motion", match = { namespace = "^nekrypta.*" }, no_anim = true })
hl.on("hyprland.start", function()
    hl.exec_cmd(command .. "start")
end)
