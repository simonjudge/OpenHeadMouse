const test = require("node:test");
const assert = require("node:assert/strict");
const {stateAt, pathMeasures, mount} = require("./viewer.js");

function trial(start, index = 0) {
    return {
        start, end: start + 1, start_pos: [0, 0], target_pos: [10, 0],
        click_pos: [11, 1], hit: true, width_px: 4, block_type: "circle", condition_label: "A",
        trial_index: index, trajectory: [[0, 0, 0], [0.5, 9, 1], [0.8, 10, 0]],
        speeds: [0, 18, 4], full_path: [[0, 0], [9, 1], [10, 0], [11, 1]],
    };
}

function recording() {
    return {
        frames: [{t: 0}, {t: 0.5}, {t: 1}], trials: [trial(2), trial(4, 1)],
        pretests: [], head_task_end: null,
    };
}

test("head starts front-on and angle labels track the coloured axis tips on seek and camera reset", t => {
    const strokes = [], labels = [];
    let path = [];
    const ctx = {
        clearRect() { strokes.length = 0; labels.length = 0; },
        beginPath() { path = []; },
        moveTo(...p) { path.push(p); },
        lineTo(...p) { path.push(p); },
        stroke() { strokes.push({color: this.strokeStyle, path}); },
        fillText(text, x, y) { labels.push({text, x, y, color: this.fillStyle}); },
        fillRect() {}, strokeRect() {},
    };
    const elements = {};
    for (const id of ["title", "pretest", "session-summary", "seek", "play", "speed",
        "head", "head-live", "cursor", "cursor-live", "time", "rewind",
        "zoom-in", "zoom-out", "reset-camera", "relative-pose", "reference-pose-source"]) {
        elements[id] = {width: 660, height: 330, getContext: () => ctx};
    }
    // Keep cursor rendering separate from the captured head draw calls.
    elements.cursor.getContext = () => ({...ctx, clearRect() {}, fillText() {}});
    elements["relative-pose"].checked = true;
    const graphStrokes = [];
    const graphFills = [];
    elements["head-graphs"] = {appendChild() {}};
    const oldWindow = global.window, oldDocument = global.document;
    global.window = {addEventListener() {}};
    global.document = {createElement() {
        const colors = [];
        const fills = [];
        graphStrokes.push(colors);
        graphFills.push(fills);
        const graphContext = {
            ...ctx, clearRect() {}, fillText() {}, beginPath() {}, moveTo() {}, lineTo() {},
            stroke() { colors.push(this.strokeStyle); },
            fillRect(...rect) { fills.push({color: this.fillStyle, rect}); },
            save() {}, restore() {}, rect() {}, clip() {}, getImageData() { return {}; }, putImageData() {},
        };
        return {setAttribute() {}, getContext: () => graphContext};
    }};
    t.after(() => { global.window = oldWindow; global.document = oldDocument; });
    const frame = {
        t: 0, rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        angles: [0, 0, 0], raw_angles: [0, 0, 0], quaternion: [1, 0, 0, 0],
        recorded_angles: [90, 0, 0],
        recorded_rotation: [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
        speed: 0, acceleration: 0, phase: "",
        excluded: true, initial_pose_excluded: true, movement_excluded: true,
    };
    const data = {
        title: "Test", synced: false, pretest_html: "", session_summary: {},
        angle_colors: {pitch: "#d62728", yaw: "#2ca02c", roll: "#1f77b4"},
        reference_pose_source: "Calibration pose",
        duration: 1, frames: [frame, {
            ...frame, t: 1, angles: [-90, 0, 0],
            excluded: false, initial_pose_excluded: false, movement_excluded: false,
            rotation: [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
            recorded_angles: [0, 0, 0], recorded_rotation: frame.rotation,
        }],
        trials: [], pretests: [], head_task_end: 1,
        head_graphs: [
            {label: "Relative yaw (deg)", color: "#2ca02c", points: [[0, 0], [1, -90]]},
            {label: "Relative pitch (deg)", color: "#d62728", points: [[0, 0], [1, 0]]},
            {label: "Angular speed (deg/s)", color: "#1f77b4",
                points: [[0, 20, true], [.25, 10, true], [.5, 2, false], [1, 1, false]]},
        ], cursor_graphs: [],
        geometry: {vertices: [], edges: []}, screen: [800, 600],
    };
    const before = JSON.stringify(data);
    const viewer = mount(data, {querySelector: selector => elements[selector.slice(1)]});
    assert.equal(elements["reference-pose-source"].textContent, "Reference pose: Calibration pose");
    assert.equal(graphStrokes[0][0], data.angle_colors.yaw);
    assert.equal(graphStrokes[1][0], data.angle_colors.pitch);
    assert.ok(!graphStrokes[2].includes("#999"));
    assert.ok(graphStrokes[2].includes("#1f77b4"));
    assert.deepEqual(graphFills[2], [{color: "#d9d9d9", rect: [58, 20, 295, 68]}]);
    assert.match(elements["head-live"].textContent, /Excluded from: initial-pose statistics and session movement statistics/);
    const originalGraphs = JSON.stringify(graphStrokes);

    assert.deepEqual(strokes.map(s => s.path), [
        [[330, 165], [455, 165]], [[330, 165], [330, 40]], [[330, 165], [330, 165]],
    ]);
    assert.deepEqual(labels, [
        {text: "Pitch +0.0 deg", x: 459, y: 161, color: "#d62728"},
        {text: "Yaw +0.0 deg", x: 334, y: 36, color: "#2ca02c"},
    ]);
    const relativeStrokes = JSON.stringify(strokes);
    elements["relative-pose"].checked = false;
    elements["relative-pose"].onchange();
    assert.equal(labels[1].text, "Yaw +90.0 deg");
    assert.notEqual(JSON.stringify(strokes), relativeStrokes);
    assert.equal(elements.seek.value, "0");
    assert.equal(viewer.state().frameIndex, 0);
    assert.equal(JSON.stringify(graphStrokes), originalGraphs);
    elements["relative-pose"].checked = true;
    elements["relative-pose"].onchange();
    assert.equal(JSON.stringify(strokes), relativeStrokes);
    elements.seek.value = "1";
    elements.seek.oninput();
    assert.equal(labels[1].text, "Yaw -90.0 deg");
    labels.forEach((label, i) => {
        assert.equal(label.x, strokes[i].path[1][0] + 4);
        assert.equal(label.y, strokes[i].path[1][1] - 4);
    });
    elements["relative-pose"].checked = false;
    elements["relative-pose"].onchange();
    assert.equal(labels[1].text, "Yaw +0.0 deg");
    assert.deepEqual(strokes[0].path, [[330, 165], [455, 165]]);
    elements["relative-pose"].checked = true;
    elements["relative-pose"].onchange();
    const original = JSON.stringify(strokes);
    elements.head.onpointerdown({clientX: 0, clientY: 0, target: {setPointerCapture() {}}});
    elements.head.onpointermove({clientX: 20, clientY: 10});
    assert.notEqual(JSON.stringify(strokes), original);
    elements["reset-camera"].onclick();
    assert.equal(JSON.stringify(strokes), original);
    elements.rewind.onclick();
    assert.equal(labels[1].text, "Yaw +0.0 deg");
    assert.deepEqual(strokes[0].path, [[330, 165], [455, 165]]);
    assert.equal(JSON.stringify(data), before);
});

test("seek uses the last head frame at or before time, including boundaries", () => {
    const data = recording();
    assert.equal(stateAt(data, 0.49).frameIndex, 0);
    assert.equal(stateAt(data, 0.5).frameIndex, 1);
    assert.equal(stateAt(data, 0.51).frameIndex, 1);
    assert.match(stateAt(data, -1).headNote, /Before/);
    assert.equal(stateAt(data, 5).frameIndex, 2);
    assert.match(stateAt(data, 5).headNote, /ended/);
});

test("before a trial, cursor starts at start position and trail is empty", () => {
    const state = stateAt(recording(), 1);
    assert.equal(state.trialIndex, 0);
    assert.deepEqual(state.cursor, [0, 0]);
    assert.deepEqual(state.points, []);
    assert.equal(state.elapsed, 0);
    assert.match(state.phase, /Before Fitts/);
});

test("partial path, exact click, inter-trial gap, next trial and end", () => {
    const data = recording();
    const partial = stateAt(data, 2.6);
    assert.deepEqual(partial.cursor, [9, 1]);
    assert.equal(partial.speed, 18);
    assert.equal(partial.points.length, 2);
    assert.equal(partial.clicked, false);
    const click = stateAt(data, 3);
    assert.equal(click.clicked, true);
    assert.deepEqual(click.cursor, [11, 1]);
    assert.deepEqual(click.points, data.trials[0].full_path);
    assert.equal(stateAt(data, 3.5).trialIndex, 0);
    assert.equal(stateAt(data, 4).trialIndex, 1);
    assert.match(stateAt(data, 6).phase, /ended/);
});

test("empty trajectories retain prior click until click endpoint arrives", () => {
    const data = recording();
    data.trials[1].trajectory = [];
    data.trials[1].speeds = [];
    assert.deepEqual(stateAt(data, 4.5).cursor, [11, 1]);
    assert.equal(stateAt(data, 4.5).speed, 0);
    assert.deepEqual(stateAt(data, 5).points, data.trials[1].full_path);
});

test("pretest playback, cancelled label and waiting gap before targets", () => {
    const data = recording();
    data.head_task_end = 1;
    data.pretests = [{
        start: 0, description: "Initial rest (cancelled)", points: [[0.1, 40, 50], [0.5, 41, 51]],
    }];
    const state = stateAt(data, 0.6);
    assert.equal(state.trialIndex, -1);
    assert.deepEqual(state.cursor, [41, 51]);
    assert.match(state.phase, /cancelled/);
    const gap = stateAt(data, 1.5);
    assert.deepEqual(gap.points, []);
    assert.equal(gap.cursor, null);
    assert.match(gap.phase, /waiting/);
    assert.equal(stateAt(data, 2).trialIndex, 0);
    data.trials = [];
    assert.equal(stateAt(data, 0.6).trialIndex, -1);
    data.pretests = [];
    data.frames = [];
    assert.equal(stateAt(data, 0).frame, null);
});

test("MacKenzie metrics use identical sign-change and sample-SD formulas", () => {
    const target = trial(0);
    const points = [[0, 0], [9, 1], [7, -1], [10, 1], [8, 0]];
    const metrics = pathMeasures(target, points);
    assert.equal(metrics.tre, 1);
    assert.equal(metrics.tac, 2);
    assert.equal(metrics.mdc, 3);
    assert.equal(metrics.odc, 3);
    assert.equal(metrics.mo_px, 0.2);
    assert.equal(metrics.me_px, 0.6);
    assert.ok(Math.abs(metrics.mv_px - Math.sqrt(0.7)) < 1e-12);
    assert.equal(pathMeasures(target, [[0, 0]]), null);
    target.target_pos = target.start_pos;
    assert.equal(pathMeasures(target, points), null);
});
