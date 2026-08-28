-- A small live-rate label that rides along with mpv's on-screen controller.
--
-- The point of this player is a frame rate you cannot see being missed, which
-- is exactly the thing a number can confirm and the eye cannot. So: show the
-- real post-filter rate, but only while the OSC bar is already up, and cost
-- nothing whatsoever the rest of the time.
--
-- "Cost nothing" is meant literally, and is why this is not a polling loop:
--
--   * Nothing is observed that ticks per frame. The trigger is mouse movement,
--     which is what raises the OSC too, so the label appears and leaves with
--     the bar rather than merely near it.
--   * While the label is up it refreshes on a timer, not on every frame. The
--     numbers are averages over roughly a second anyway, so redrawing faster
--     would burn GPU on text that has not changed.
--   * While the label is down the timer is stopped and the overlay is removed,
--     not just blanked. An empty overlay still goes through libass on every
--     rendered frame; a removed one does not exist.
--
-- Every value here is read from a property mpv already maintains for its own
-- bookkeeping. Nothing new is measured, so displaying it costs the read.

local UPDATE_INTERVAL = 1.0   -- seconds; the properties are ~1s averages
local HIDE_AFTER      = 0.5   -- matches the OSC's own default hidetimeout

local overlay = mp.create_osd_overlay("ass-events")
-- Fix a virtual canvas so the label sits in the same place at any window size.
overlay.res_x, overlay.res_y = 1920, 1080

local refresh_timer, hide_timer
local shown = false

local function label()
    local out  = mp.get_property_number("estimated-vf-fps")
    local src  = mp.get_property_number("container-fps")
    local disp = mp.get_property_number("display-fps")
    -- Two different failures, both of which look like judder, so they are
    -- summed: "frame-drop-count" is the VO dropping a frame that missed its
    -- vsync, "decoder-frame-drop-count" is the decoder giving up because the
    -- filter chain fell behind. (There is no "vo-drop-frame-count" in mpv --
    -- asking for it returns nil, which silently reads as zero drops forever.)
    local drops = (mp.get_property_number("frame-drop-count", 0) or 0)
                + (mp.get_property_number("decoder-frame-drop-count", 0) or 0)

    if not out then return nil end

    local parts = {string.format("%.4g", src or 0) .. " to " .. string.format("%.1f", out) .. " fps"}
    if disp then
        parts[#parts + 1] = string.format("%.0f Hz", disp)
    end
    -- Silence is the good case; only spend pixels on drops once there are some.
    if drops > 0 then
        parts[#parts + 1] = string.format("drops %d", drops)
    end
    return table.concat(parts, "   ")
end

local function draw()
    local text = label()
    mp.msg.verbose("draw: " .. tostring(text))
    if not text then return end
    -- Long-bracket string, not quotes: every ASS override tag is backslash
    -- prefixed, and [[...]] takes those literally instead of trying to read
    -- them as Lua escapes (\a is not one, and the script refuses to load).
    overlay.data = string.format(
        [[{\an3\pos(1892,986)\fs21\bord1.4\shad0\1c&HFFFFFF&\3c&H000000&\alpha&H30&}%s]],
        text
    )
    overlay:update()
end

local function hide()
    if not shown then return end
    shown = false
    if refresh_timer then refresh_timer:kill(); refresh_timer = nil end
    -- remove(), not data="": a blank overlay is still composited every frame.
    overlay:remove()
end

local function show()
    mp.msg.verbose("show (shown=" .. tostring(shown) .. ")")
    if hide_timer then hide_timer:kill() end
    hide_timer = mp.add_timeout(HIDE_AFTER, hide)

    if shown then return end
    shown = true
    draw()
    refresh_timer = mp.add_periodic_timer(UPDATE_INTERVAL, draw)
end

-- Mouse movement is what summons the OSC, so it is what summons this.
mp.observe_property("mouse-pos", "native", show)

-- The OSC also appears on pause and after a seek, with no mouse involved.
mp.observe_property("pause", "bool", show)
mp.register_event("seek", show)

-- Nothing to display, and nothing to keep alive, once playback is over.
mp.register_event("end-file", hide)
