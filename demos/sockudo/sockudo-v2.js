import Sockudo from "https://cdn.jsdelivr.net/npm/@sockudo/client@2.1.0/dist/web/sockudo.mjs";

export function createSockudoClient(channelAuthorization) {
  const options = {
    wsHost: "127.0.0.1",
    wsPort: 6001,
    wssPort: 6001,
    forceTLS: false,
    encrypted: false,
    cluster: "local",
    enabledTransports: ["ws"],
    disabledTransports: ["xhr_streaming", "xhr_polling", "sockjs"],
    disableStats: true,
    protocolVersion: 2,
    connectionRecovery: true,
    messageDeduplication: true,
  };
  if (channelAuthorization) {
    options.channelAuthorization = channelAuthorization;
  }
  return new Sockudo("app-key", options);
}

export function serialTag(client, channelName) {
  const pos = client.getRecoveryPosition(channelName);
  if (!pos || pos.serial == null) return "";
  return " · #" + pos.serial;
}

export function onSubscribed(channel, callback) {
  channel.bind("sockudo:subscription_succeeded", callback);
}

export function onSubscribeError(channel, callback) {
  channel.bind("sockudo:subscription_error", callback);
}

export function bindResume(client, log) {
  client.bind("sockudo:resume_success", (payload) => {
    const recovered = (payload && payload.recovered) || [];
    const names = recovered.map((item) => item.channel).filter(Boolean);
    log("v2 resume ok" + (names.length ? " " + names.join(", ") : ""));
  });
  client.bind("sockudo:resume_failed", (payload) => {
    log("v2 resume failed " + JSON.stringify(payload));
  });
}

export function wireDropResume(client, { log, setBadge, dropBtn, resumeBtn, onDropped }) {
  dropBtn.onclick = () => {
    client.disconnect();
    dropBtn.disabled = true;
    resumeBtn.disabled = false;
    setBadge("dropped", false);
    log("WS dropped — other card থেকে পাঠাও, তারপর Resume");
    if (onDropped) onDropped();
  };
  resumeBtn.onclick = () => {
    resumeBtn.disabled = true;
    dropBtn.disabled = false;
    setBadge("reconnecting", null);
    log("reconnecting with last serial…");
    client.connect();
  };
}
