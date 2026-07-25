-- HTTP control surface for a headless instance.
--
-- A `-no-ui` instance has no GLFW, so nothing ever writes its pad's buttonStatus and it stays at
-- 0xffff (nothing pressed). Pad overrides are ANDed into that on every read, so they are the only
-- route by which such an instance can be driven at all.
--
-- Handlers registered here are dispatched by the emulator's /api/v1/lua/<name> route.
--
--   GET /api/v1/lua/pad?button=cross&state=down
--   GET /api/v1/lua/pad?button=cross&state=up
--   GET /api/v1/lua/release
--
-- State is deliberately down/up rather than a timed tap: holding a button for N milliseconds inside
-- the handler would stall the emulator thread for N milliseconds. The caller owns the timing.

PCSX.WebServer = PCSX.WebServer or {}
PCSX.WebServer.Handlers = PCSX.WebServer.Handlers or {}

local BUTTON = PCSX.CONSTS.PAD.BUTTON

local NAMES = {
  select = BUTTON.SELECT, start = BUTTON.START,
  up = BUTTON.UP, right = BUTTON.RIGHT, down = BUTTON.DOWN, left = BUTTON.LEFT,
  l1 = BUTTON.L1, l2 = BUTTON.L2, r1 = BUTTON.R1, r2 = BUTTON.R2,
  triangle = BUTTON.TRIANGLE, circle = BUTTON.CIRCLE, cross = BUTTON.CROSS, square = BUTTON.SQUARE,
}

local function parseQuery(query)
  local out = {}
  for k, v in string.gmatch(query or '', '([^&=?]+)=([^&=?]+)') do out[k] = v end
  return out
end

local function pad()
  return PCSX.SIO0.slots[1].pads[1]
end

PCSX.WebServer.Handlers.pad = function(req)
  local q = parseQuery(req.urlData.query)
  local bit = NAMES[string.lower(q.button or '')]
  if bit == nil then
    return 'HTTP/1.1 400 Bad Request\r\n\r\nunknown button: ' .. tostring(q.button) .. '\r\n'
  end
  -- Called with a dot, not a colon: the binding takes the button as its first argument, so passing
  -- an implicit self ahead of it is what "Invalid argument to setOverride" means.
  if q.state == 'up' then
    pad().clearOverride(bit)
  else
    pad().setOverride(bit)
  end
  return (q.state or 'down') .. ' ' .. q.button .. '\n'
end

-- A crashed or interrupted caller can leave a button held down forever; this is the recovery.
PCSX.WebServer.Handlers.release = function(req)
  for _, bit in pairs(NAMES) do pad().clearOverride(bit) end
  return 'all released\n'
end

PCSX.WebServer.Handlers.ping = function(req)
  return 'ok\n'
end
