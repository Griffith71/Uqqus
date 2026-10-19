// The microphone and camera of this window (helpers/calls.py). Video is small on purpose: in a mesh every person
// uploads one copy per other person, so 640x360 at up to 24 frames keeps a 4-way call within an ordinary uplink.

export const AUDIO_CONSTRAINTS = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
export const VIDEO_CONSTRAINTS = { width: { ideal: 640 }, height: { ideal: 360 }, frameRate: { ideal: 24, max: 24 } };
export const MAX_VIDEO_BITRATE = 500_000;       // bits per second per peer
export const MAX_AUDIO_BITRATE = 40_000;

export class MediaRefused extends Error {}

function explain(error) {
  const name = error && error.name;
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "The browser didn't allow the microphone. Allow it for this site in the address bar, then try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone was found.";
  if (name === "NotReadableError") return "The microphone is in use by another app.";
  return "The microphone couldn't be started.";
}

export class LocalMedia {
  constructor(stream, wantsVideo) {
    this.stream = stream;
    this.wantsVideo = wantsVideo;
    this.muted = false;
    this.cameraOff = !wantsVideo;
    this.listeners = new Set();
  }

  // video: whether to ask for the camera (the server may have said audio only)
  static async open(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new MediaRefused("This browser can't make calls.");
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: AUDIO_CONSTRAINTS, video: video ? VIDEO_CONSTRAINTS : false });
      return new LocalMedia(stream, video);
    } catch (error) {
      if (video) {
        // no camera, or it was refused: join with the microphone alone
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: AUDIO_CONSTRAINTS, video: false });
          const media = new LocalMedia(stream, false);
          media.cameraProblem = true;
          return media;
        } catch (second) {
          throw new MediaRefused(explain(second));
        }
      }
      throw new MediaRefused(explain(error));
    }
  }

  get audioTrack() {
    return this.stream.getAudioTracks()[0] || null;
  }

  get videoTrack() {
    return this.stream.getVideoTracks()[0] || null;
  }

  onChange(fn) {
    this.listeners.add(fn);
  }

  changed(kind, track) {
    for (const fn of this.listeners) fn(kind, track);
  }

  setMuted(muted) {
    this.muted = muted;
    if (this.audioTrack) this.audioTrack.enabled = !muted;
  }

  setCameraOff(off) {
    this.cameraOff = off;
    if (this.videoTrack) this.videoTrack.enabled = !off;
  }

  async devices() {
    const all = await navigator.mediaDevices.enumerateDevices();
    return {
      audio: all.filter((d) => d.kind === "audioinput"),
      video: all.filter((d) => d.kind === "videoinput"),
    };
  }

  // a different microphone or camera: the new track replaces the old one in every connection (mesh.replaceTrack)
  async useDevice(kind, deviceId) {
    const constraints = kind === "audio"
      ? { audio: { ...AUDIO_CONSTRAINTS, deviceId: { exact: deviceId } }, video: false }
      : { audio: false, video: { ...VIDEO_CONSTRAINTS, deviceId: { exact: deviceId } } };
    const fresh = await navigator.mediaDevices.getUserMedia(constraints);
    const track = kind === "audio" ? fresh.getAudioTracks()[0] : fresh.getVideoTracks()[0];
    const old = kind === "audio" ? this.audioTrack : this.videoTrack;
    if (old) {
      this.stream.removeTrack(old);
      old.stop();
    }
    this.stream.addTrack(track);
    track.enabled = kind === "audio" ? !this.muted : !this.cameraOff;
    this.changed(kind, track);
    return track;
  }

  // a Space speaker turning their camera on or off after joining with the microphone alone (mesh.syncTracks then
  // adds or takes away the video in every connection)
  async startCamera() {
    if (this.videoTrack) {
      this.setCameraOff(false);
      return this.videoTrack;
    }
    let fresh;
    try {
      fresh = await navigator.mediaDevices.getUserMedia({ audio: false, video: VIDEO_CONSTRAINTS });
    } catch (error) {
      const name = error && error.name;
      if (name === "NotAllowedError" || name === "SecurityError") {
        throw new MediaRefused("The browser didn't allow the camera. Allow it for this site in the address bar, then try again.");
      }
      if (name === "NotFoundError" || name === "OverconstrainedError") throw new MediaRefused("No camera was found.");
      if (name === "NotReadableError") throw new MediaRefused("The camera is in use by another app.");
      throw new MediaRefused("The camera couldn't be started.");
    }
    const track = fresh.getVideoTracks()[0];
    this.stream.addTrack(track);
    this.wantsVideo = true;
    this.cameraOff = false;
    return track;
  }

  stopCamera() {
    const track = this.videoTrack;
    if (!track) return;
    this.stream.removeTrack(track);
    track.stop();
    this.wantsVideo = false;
    this.cameraOff = true;
  }

  stop() {
    for (const track of this.stream.getTracks()) track.stop();
  }
}

// How loud a stream is right now (0..1), for the speaking ring round a tile. One AudioContext for the window.
let audioContext = null;

export function levelMeter(stream) {
  if (!stream.getAudioTracks().length) return () => 0;
  try {
    audioContext = audioContext || new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    return () => {
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
      return peak / 128;
    };
  } catch (e) {
    return () => 0;
  }
}

export function resumeAudio() {
  if (audioContext && audioContext.state === "suspended") audioContext.resume().catch(() => {});
}
