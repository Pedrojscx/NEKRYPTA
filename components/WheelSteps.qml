import QtQuick

// Wheel and touchpad scrolling as whole steps. A mouse notch is 120 units of angle; a touchpad sends many small
// deltas, which add up here and only count once they cross 120, so a light swipe doesn't jump the level.
// The event is always consumed: nothing behind (a scrolling panel, the workspaces) moves with it.
WheelHandler {
    id: root
    signal stepped(int steps)
    property real carried: 0
    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    onWheel: event => {
        // Vertical scrolling, or horizontal on touchpads that only send x.
        const delta = event.angleDelta.y !== 0 ? event.angleDelta.y : -event.angleDelta.x;
        event.accepted = true;
        if (delta === 0)
            return;
        // A change of direction starts over instead of cancelling what was carried.
        if (carried !== 0 && Math.sign(carried) !== Math.sign(delta))
            carried = 0;
        carried += delta;
        const steps = carried > 0 ? Math.floor(carried / 120) : Math.ceil(carried / 120);
        if (steps === 0)
            return;
        carried -= steps * 120;
        root.stepped(steps);
    }
}
