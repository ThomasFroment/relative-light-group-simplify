# Relative Light Group Simplified

A Home Assistant light group whose brightness slider behaves like a room slider in the Hue app.
When you move the group brightness, every lamp that is on moves **relative to the others**, instead of all lamps jumping to the same value.

This is a simplified fork of [Relative Light Group](https://github.com/Cheerpipe/ha-relative-light-group) by Felipe Urzúa.
It has no options to configure: one behaviour, described below, chosen to feel natural for everyday use.

## Installation

### HACS

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/ThomasFroment/relative-light-group-simplify` with the category **Integration**.
3. Install **Relative Light Group Simplified**, then restart Home Assistant.

### Manual

1. Copy `custom_components/relative_light_group_simplified` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Helpers → Create helper**.
2. Choose **Relative Light Group Simplified**.
3. Enter a name and pick the lights.

To change which lights belong to the group, open the helper and use **Configure**.
A group cannot contain itself, and it cannot contain another Relative Light Group Simplified.

## How the group behaves

The examples use two lamps: **A at 100 %** and **B at 40 %**.

### Changing the brightness

- **Only lamps that are on are adjusted.** Lamps that are off stay off.
- **Going up**, every lamp moves toward 100 % so that they all reach 100 % at the same time.
  Example: group to 80 % → A 100 %, B 60 %.
- **Going down**, every lamp is scaled down proportionally, so they all reach the minimum at the same time.
  Example: group to 50 % → A 72 %, B 29 %.
- **The group remembers the ratio between lamps, even after they meet at a limit.**
  Example: group up to 100 % (A and B are both at 100 %), then back down to 50 % → A 72 %, B 29 % again.
  The same applies at the minimum.
- The group's brightness is the **average** of the lamps that are on.
- All lamps receive their command **at the same time**, so transitions stay in step.
- Lamps that can only be switched on or off are not affected by brightness changes.

### Changing a lamp outside the group

If any lamp is changed without going through the group (its own app, a wall switch, an automation targeting that lamp), **the current levels of all lamps become the new ratio**.

Example: the group remembers A 80 % : B 20 %, and both lamps are at 100 % because the group was pushed to max.
You set B to 20 % in its own app. From now on the group uses A 100 % : B 20 %.

A lamp turned on by hand while the group is on joins the ratio at its current level.

### Colour, colour temperature and effects

- These are sent **only to lamps that are on**. Lamps that are off stay off.
- They do not change the remembered brightness ratio.

### Turning the group on

- **Without a brightness** (tap the group, `light.turn_on` with no brightness):
  **all** lamps in the group turn on, each at its own last brightness.
  The remembered ratio is kept. A lamp that was not part of it joins at its current level.
- **With a brightness** (an automation sets 50 %, or you drag the slider while the group is off):
  **every lamp is set to that same value**, and this **replaces the remembered ratio**.
  The lamps then move together until something changes a lamp outside the group.

### Turning the group off

All lamps in the group turn off.

### Group state

- The group is **on** when at least one lamp is on, and **off** when all lamps are off.
- Lamps that are unavailable, unknown or disabled are ignored. The group is unavailable only when no lamp can be used.
- Colour, colour temperature and effect shown on the group come from the lamps that are on.

### No slider jumping

For 2 seconds after a group command, the group shows the value you asked for and ignores the lamps' own updates caused by that command.
This stops the slider from bouncing while the lamps catch up.
Changes made outside the group are still shown immediately.

### After a Home Assistant restart

The remembered ratio is not saved.
After a restart, the lamps' current levels become the new ratio.

## Fine-tuning

There is nothing to configure in the UI.
If you want to change a default, edit `custom_components/relative_light_group_simplified/const.py`, for example `DEFAULT_DEBOUNCE_TIME` (2000 ms) for the no-jumping window.

## Known issues

The current code does not yet fully match the behaviour above:

- **The ratio is sometimes forgotten** after the group reaches 100 % or after the group is turned off and on again.
  This happens when the group contains an on/off-only lamp that is on, or a lamp that was already at the level the group asked for.
- **Turning on from off with a brightness keeps the old ratio** instead of replacing it, so the next slider move spreads the lamps apart again.
- **Lamps are updated one after another** instead of at the same time.
- Some configuration error messages show "Translation Error" instead of the real message.

## Credits

Based on [Relative Light Group](https://github.com/Cheerpipe/ha-relative-light-group) by Felipe Urzúa, released under the MIT licence, which is itself built on the Home Assistant `group` integration.
