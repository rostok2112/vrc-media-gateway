(async function waitForSpicetify() {
    while (!window.Spicetify || !Spicetify.Player) {
        await new Promise(r => setTimeout(r, 500));
    }

    console.log("Spicetify ready");

    const WS_URL = "ws://127.0.0.1:8080/api/ws/spotify";
    let ws;

    function connect() {
        ws = new WebSocket(WS_URL);

        ws.onopen = () => console.log("WS connected");
        ws.onclose = () => { console.log("WS closed, retrying..."); setTimeout(connect, 2000); };

        ws.onmessage = async (ev) => {
            try {
                const msg = JSON.parse(ev.data);
                console.log("WS IN:", msg);

                if (msg.type === "rpc_request") {
                    await handleRpc(msg);
                    return;
                }
                if (msg.action === "play") {
                    try { Spicetify.Player.playUri(msg.uri); } catch(e){console.error(e);}
                }
                if (msg.action === "seek") {
                    try { Spicetify.Player.seek(msg.position_ms); } catch(e){console.error(e);}
                }
                if (msg.action === "pause") {
                    try { Spicetify.Player.pause(); } catch(e){console.error(e);}
                }
            } catch (e) {
                console.error("WS parse error", e);
            }
        };
    }

    async function handleRpc(msg) {
        let result = null;
        try {
            if (msg.method === "load") {
                await Spicetify.Player.playUri(msg.params.uri);
                await new Promise(r => setTimeout(r, 350));
                try { Spicetify.Player.pause(); } catch(e){}
                result = { ok: true };
            }

            if (msg.method === "metadata") {
                let duration = 0;
                try {
                    const data = Spicetify.Player.data;
                    if (data && data.item && data.item.duration) duration = data.item.duration.milliseconds || 0;
                } catch(e) {}
                result = { duration_ms: duration };
            }

            if (msg.method === "seek_play") {
                const pos = msg.params.position_ms || 0;
                try { Spicetify.Player.seek(pos); } catch(e){}
                try { Spicetify.Player.play(); } catch(e){}
                result = { ok: true };
            }
        } catch (e) { console.error("handleRpc error", e); result = null; }

        try {
            ws.send(JSON.stringify({ type: "rpc_response", id: msg.id, result: result }));
        } catch(e) { console.error("failed to send rpc_response", e); }
    }

    // Polling can stay for UI only — but DO NOT send playback_ended
    setInterval(() => {
        try {
            // keep local monitoring for logs/metrics if you want, but do NOT trigger stop
            // (intentionally empty or used only for internal UI)
        } catch (e) {}
    }, 1000);

    connect();
})();
