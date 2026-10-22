"use strict";

const NotebookPlayback = (() => {
    function lastAtOrBefore(items, time, key) {
        let lo = 0, hi = items.length;
        while (lo < hi) {
            const mid = (lo + hi) >>> 1;
            if (key(items[mid]) <= time) lo = mid + 1;
            else hi = mid;
        }
        return lo - 1;
    }

    function pathMeasures(trial, points) {
        const dx = trial.target_pos[0] - trial.start_pos[0];
        const dy = trial.target_pos[1] - trial.start_pos[1];
        const length = Math.hypot(dx, dy);
        if (points.length < 2 || !length) return null;
        const x = [], y = [];
        let entries = 0, wasInside = false;
        for (const p of points) {
            const rx = p[0] - trial.start_pos[0], ry = p[1] - trial.start_pos[1];
            x.push((rx * dx + ry * dy) / length);
            y.push((-rx * dy + ry * dx) / length);
            const inside = Math.hypot(p[0] - trial.target_pos[0], p[1] - trial.target_pos[1]) <= trial.width_px / 2;
            if (inside && !wasInside) entries++;
            wasInside = inside;
        }
        const changes = values => {
            const signs = values.map(Math.sign).filter(s => s !== 0);
            return signs.slice(1).filter((s, i) => s !== signs[i]).length;
        };
        const diff = values => values.slice(1).map((v, i) => v - values[i]);
        const mean = y.reduce((a, b) => a + b, 0) / y.length;
        return {
            tre: Math.max(0, entries - 1), tac: changes(y),
            mdc: changes(diff(y)), odc: changes(diff(x)),
            mv_px: Math.sqrt(y.reduce((a, v) => a + (v - mean) ** 2, 0) / (y.length - 1)),
            me_px: y.reduce((a, v) => a + Math.abs(v), 0) / y.length, mo_px: mean,
        };
    }

    function stateAt(data, time) {
        // Compare on the original clock to preserve exact sample boundaries.
        const origin = data.clock_origin ?? 0;
        const clock = time + origin;
        const frameIndex = Math.max(0, lastAtOrBefore(data.frames, clock, f => f.t + origin));
        const frame = data.frames[frameIndex] || null;
        const headNote = !frame ? "No head recording" : clock < data.frames[0].t + origin ?
            "Before head recording starts" : clock > data.frames.at(-1).t + origin ? "Head recording has ended" : "";
        if (!data.trials.length || (data.head_task_end !== null && clock < data.trials[0].start + origin)) {
            const record = [...data.pretests].reverse().find(r => r.start + origin <= clock) || data.pretests[0];
            const after = data.head_task_end !== null && clock > data.head_task_end + origin;
            const points = after ? [] : (record?.points || []).filter(p => p[0] + origin <= clock).map(p => p.slice(1));
            return {
                frame, frameIndex, headNote, trialIndex: -1, points,
                cursor: points.at(-1) || null, clicked: false,
                phase: after ? "Head-movement exercise finished - waiting for the mouse task to start" :
                    record?.description || "No cursor recording",
            };
        }
        const index = Math.max(0, lastAtOrBefore(data.trials, clock, t => t.start + origin));
        const trial = data.trials[index];
        const clicked = clock >= trial.end + origin;
        const count = lastAtOrBefore(trial.trajectory, clock, p => trial.start + origin + p[0]) + 1;
        let points = clicked ? trial.full_path : trial.trajectory.slice(0, count).map(p => p.slice(1));
        const cursor = points.at(-1) || (index > 0 ? data.trials[index - 1].click_pos : trial.start_pos);
        if (clock < data.trials[0].start + origin) points = [];
        return {
            frame, frameIndex, headNote, trialIndex: index, trial, points, cursor, clicked,
            speed: clicked ? trial.speeds.at(-1) || 0 : trial.speeds[count - 1] || 0,
            elapsed: Math.min(Math.max(clock, trial.start + origin), trial.end + origin) - (trial.start + origin),
            distance: Math.hypot(cursor[0] - trial.target_pos[0], cursor[1] - trial.target_pos[1]),
            measures: pathMeasures(trial, points),
            phase: clock < data.trials[0].start + origin ? "Before Fitts session starts" :
                index === data.trials.length - 1 && clock > trial.end + origin ? "Fitts session has ended" : "Fitts mouse task",
        };
    }

    function mount(data, root) {
        const get = id => root.querySelector(`#${id}`);
        get("title").textContent = data.title + (data.synced ? " (synchronised)" : "");
        get("pretest").innerHTML = data.pretest_html;
        get("session-summary").textContent = Object.entries(data.session_summary)
            .map(([key, value]) => `${key}: ${typeof value === "number" ? value.toFixed(1) : value ?? "n/a"}`).join("\n");
        let time = 0, playing = false, zoom = 1, timer = null;
        let cameraYaw = 0, cameraPitch = 0, cameraScale = 1;
        const slider = get("seek"), play = get("play"), speed = get("speed");
        slider.max = String(data.duration);
        const graphs = [];
        for (const [id, series] of [["head-graphs", data.head_graphs], ["cursor-graphs", data.cursor_graphs]]) {
            for (const item of series) {
                const canvas = document.createElement("canvas");
                canvas.width = 660;
                canvas.height = 100;
                canvas.setAttribute("aria-label", item.label);
                get(id).appendChild(canvas);
                const values = item.points.filter(Boolean).map(p => p[1]);
                let low = values.length ? values.reduce((a, b) => Math.min(a, b)) : 0;
                let high = values.length ? values.reduce((a, b) => Math.max(a, b)) : 1;
                const pad = high > low ? (high - low) * 0.03 : 1;
                low -= pad; high += pad;
                graphs.push({canvas, ...item, low, high});
            }
        }
        function line(ctx, points, color, width = 1) {
            if (!points.length) return;
            ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath();
            points.forEach((p, i) => i ? ctx.lineTo(...p) : ctx.moveTo(...p));
            ctx.stroke();
        }
        function cross(ctx, p, size, color, plus = false) {
            const [x, y] = p;
            line(ctx, [[x - size, y - (plus ? 0 : size)], [x + size, y + (plus ? 0 : size)]], color, 2);
            line(ctx, [[x - (plus ? 0 : size), y + size], [x + (plus ? 0 : size), y - size]], color, 2);
        }
        function head(state) {
            const canvas = get("head"), ctx = canvas.getContext("2d");
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (!state.frame) { ctx.fillText("No head recording", 20, 25); return; }
            const rotation = state.frame.rotation;
            function project(v) {
                const p = rotation.map(row => row.reduce((sum, r, i) => sum + r * v[i], 0));
                const x = p[0] * Math.cos(cameraYaw) + p[2] * Math.sin(cameraYaw);
                const depth = -p[0] * Math.sin(cameraYaw) + p[2] * Math.cos(cameraYaw);
                const y = p[1] * Math.cos(cameraPitch) - depth * Math.sin(cameraPitch);
                const z = p[1] * Math.sin(cameraPitch) + depth * Math.cos(cameraPitch);
                const scale = 400 * cameraScale / Math.max(0.2, 4 - z);
                return [canvas.width / 2 + x * scale, canvas.height / 2 - y * scale];
            }
            const vertices = data.geometry.vertices.map(project);
            for (const [a, b] of data.geometry.edges) line(ctx, [vertices[a], vertices[b]], "#222");
            const colors = ["#d62728", "#2ca02c", "#1f77b4"];
            colors.forEach((color, i) => {
                const tip = [0, 0, 0]; tip[i] = 1.25;
                line(ctx, [project([0, 0, 0]), project(tip)], color, 3);
            });
            const f = state.frame;
            get("head-live").textContent = [
                state.headNote, `Time: ${f.t.toFixed(1)} s`, f.phase,
                `Quaternion [w,x,y,z]: ${f.quaternion.map(v => v.toFixed(1)).join(", ")}`,
                `Relative yaw / pitch / roll: ${f.angles.map(v => v.toFixed(1)).join(" / ")} deg`,
                `Absolute yaw / pitch / roll: ${f.raw_angles.map(v => v.toFixed(1)).join(" / ")} deg`,
                `Angular speed: ${f.speed.toFixed(1)} deg/s`, `Acceleration: ${f.acceleration.toFixed(1)} deg/s2`,
            ].filter(Boolean).join("\n");
        }
        function cursor(state) {
            const canvas = get("cursor"), ctx = canvas.getContext("2d");
            const [sw, sh] = data.screen;
            const scale = Math.min((canvas.width - 20) / sw, (canvas.height - 20) / sh);
            const ox = (canvas.width - sw * scale) / 2, oy = (canvas.height - sh * scale) / 2;
            const point = p => [ox + p[0] * scale, oy + p[1] * scale];
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = "#fafafa"; ctx.fillRect(ox, oy, sw * scale, sh * scale);
            ctx.strokeStyle = "#888"; ctx.strokeRect(ox, oy, sw * scale, sh * scale);
            function click(trial, size, faded = false) {
                cross(ctx, point(trial.click_pos), size, trial.hit ?
                    (faded ? "#28a02866" : "#28a028") : (faded ? "#d2282866" : "#d22828"));
            }
            const trial = state.trial;
            if (trial) {
                for (const previous of data.trials.slice(0, state.trialIndex)) {
                    if (previous.block_type === trial.block_type && previous.condition_label === trial.condition_label) {
                        line(ctx, previous.full_path.map(point), "#96969646");
                        click(previous, 3, true);
                    }
                }
                ctx.setLineDash([5, 5]);
                line(ctx, [point(trial.start_pos), point(trial.target_pos)], "#888");
                ctx.setLineDash([]);
                const [x, y] = point(trial.target_pos);
                ctx.beginPath(); ctx.arc(x, y, trial.width_px / 2 * scale, 0, Math.PI * 2);
                ctx.fillStyle = "#1f77b432"; ctx.fill(); ctx.strokeStyle = "#1f77b4"; ctx.lineWidth = 2; ctx.stroke();
                cross(ctx, point(trial.start_pos), 5, "#555", true);
            } else {
                ctx.fillStyle = "#333"; ctx.font = "13px sans-serif"; ctx.textAlign = "center";
                ctx.fillText(state.phase, canvas.width / 2, canvas.height / 2, canvas.width - 30);
                ctx.textAlign = "start";
            }
            line(ctx, state.points.map(point), "#ff7f0e", 2);
            if (state.clicked) click(trial, 6);
            if (state.cursor) {
                const [x, y] = point(state.cursor);
                ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
                ctx.fillStyle = "#d62728"; ctx.fill(); ctx.strokeStyle = "#000"; ctx.stroke();
            }
            const lines = [state.phase, `Time: ${time.toFixed(1)} s`];
            if (trial) {
                lines.push(`Trial: ${state.trialIndex + 1} / ${data.trials.length}`,
                    `${trial.block_type}: ${trial.condition_label}`,
                    `Outcome: ${state.clicked ? trial.hit ? "HIT" : "MISS" : "in progress"}`,
                    `Elapsed: ${state.elapsed.toFixed(1)} s; MT: ${trial.movement_time_s.toFixed(1)} s`,
                    `Cursor: ${state.cursor.map(v => v.toFixed(1)).join(", ")} px`,
                    `Speed: ${state.speed.toFixed(1)} px/s; Distance: ${state.distance.toFixed(1)} px`);
                for (const [key, value] of Object.entries(state.measures || {})) lines.push(`${key}: ${value.toFixed(1)}`);
            } else lines.push(`Cursor points: ${state.points.length}`);
            get("cursor-live").textContent = lines.join("\n");
        }
        function drawGraphs(state) {
            for (const graph of graphs) {
                const {canvas, label, points, low, high} = graph;
                const ctx = canvas.getContext("2d", {willReadFrequently: true}), center = (low + high) / 2;
                const half = (high - low) / 2 / zoom;
                const x = t => 58 + t / Math.max(data.duration, 0.001) * (canvas.width - 70);
                const y = v => 88 - (v - center + half) / (2 * half) * 64;
                const isCursor = label.startsWith("Cursor") || label.startsWith("Distance");
                // Traces are fixed; only the playhead changes on each 40 ms tick.
                if (graph.cachedZoom !== zoom) {
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                    ctx.fillStyle = "#333"; ctx.font = "11px sans-serif";
                    ctx.fillText(label, 58, 12);
                    ctx.fillText((center + half).toFixed(1), 2, 27);
                    ctx.fillText((center - half).toFixed(1), 2, 88);
                    ctx.fillText("0.0 s", 58, 99); ctx.fillText(`${data.duration.toFixed(1)} s`, canvas.width - 65, 99);
                    ctx.save(); ctx.beginPath(); ctx.rect(58, 20, canvas.width - 70, 68); ctx.clip();
                    if (isCursor) {
                        for (const trial of data.trials) {
                            ctx.setLineDash([4, 3]); line(ctx, [[x(trial.start), 20], [x(trial.start), 88]], "#2e7d3280");
                            ctx.setLineDash([1, 3]); line(ctx, [[x(trial.end), 20], [x(trial.end), 88]], "#8e24aa80");
                        }
                        ctx.setLineDash([]);
                    }
                    let segment = [];
                    for (const p of points) {
                        if (p) segment.push([x(p[0]), y(p[1])]);
                        else { line(ctx, segment, "#1f77b4"); segment = []; }
                    }
                    line(ctx, segment, "#1f77b4");
                    ctx.restore();
                    graph.background = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    graph.cachedZoom = zoom;
                } else {
                    ctx.putImageData(graph.background, 0, 0);
                }
                const playhead = isCursor ? time : state.frame?.t ?? time;
                ctx.save(); ctx.beginPath(); ctx.rect(58, 20, canvas.width - 70, 68); ctx.clip();
                line(ctx, [[x(playhead), 20], [x(playhead), 88]], "#d62728", 2);
                ctx.restore();
            }
        }
        function render() {
            const state = stateAt(data, time);
            head(state); cursor(state); drawGraphs(state);
            slider.value = String(time);
            get("time").textContent = `${time.toFixed(1)} / ${data.duration.toFixed(1)} s`;
        }
        function pause() {
            playing = false; clearInterval(timer); timer = null; play.textContent = "Play";
        }
        play.onclick = () => {
            if (playing) return pause();
            if (data.duration <= 0) return;
            if (time >= data.duration) time = 0;
            playing = true; play.textContent = "Pause";
            timer = setInterval(() => {
                if (!speed.checkValidity() || !speed.value) { pause(); speed.reportValidity(); return; }
                time = Math.min(data.duration, time + 0.04 * Number(speed.value));
                render();
                if (time >= data.duration) pause();
            }, 40);
        };
        slider.oninput = () => { pause(); time = Number(slider.value); render(); };
        get("rewind").onclick = () => { pause(); time = 0; render(); };
        get("zoom-in").onclick = () => { zoom = Math.min(20, zoom * 1.25); render(); };
        get("zoom-out").onclick = () => { zoom = Math.max(1, zoom / 1.25); render(); };
        get("reset-camera").onclick = () => { cameraYaw = cameraPitch = 0; cameraScale = 1; render(); };
        let drag = null;
        get("head").onpointerdown = event => { drag = [event.clientX, event.clientY]; event.target.setPointerCapture(event.pointerId); };
        get("head").onpointerup = () => { drag = null; };
        get("head").onpointermove = event => {
            if (!drag) return;
            cameraYaw += (event.clientX - drag[0]) * 0.01;
            cameraPitch += (event.clientY - drag[1]) * 0.01;
            drag = [event.clientX, event.clientY]; render();
        };
        get("head").onwheel = event => {
            event.preventDefault();
            cameraScale = Math.max(0.3, Math.min(3, cameraScale * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
            render();
        };
        window.addEventListener("pagehide", pause);
        render();
        return {state: () => stateAt(data, time), pause};
    }
    return {stateAt, pathMeasures, mount};
})();

if (typeof module !== "undefined") module.exports = NotebookPlayback;
