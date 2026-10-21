const test = require("node:test");
const assert = require("node:assert/strict");
const {stateAt, pathMeasures} = require("./viewer.js");

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
