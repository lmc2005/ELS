export const AUTO_MIC_ID = 'auto';
export const MIC_STORAGE_KEY = 'els-preferred-audio-input';

export interface AudioInputOption {
  deviceId: string;
  label: string;
}

export function getStoredAudioDeviceId() {
  return window.localStorage.getItem(MIC_STORAGE_KEY) || AUTO_MIC_ID;
}

export function storeAudioDeviceId(deviceId: string) {
  window.localStorage.setItem(MIC_STORAGE_KEY, deviceId || AUTO_MIC_ID);
}

export async function listAudioInputDevices(): Promise<AudioInputOption[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label || `Microphone ${index + 1}`,
    }));
}

export function isIPhoneLikeMic(label: string) {
  const value = label.toLowerCase();
  return value.includes('iphone') || value.includes('ipad') || value.includes('continuity');
}

function isMacLikeMic(label: string) {
  const value = label.toLowerCase();
  return (
    value.includes('macbook') ||
    value.includes('built-in') ||
    value.includes('built in') ||
    value.includes('internal') ||
    value.includes('內建')
  );
}

export function pickPreferredAudioInput(devices: AudioInputOption[], selectedDeviceId = AUTO_MIC_ID) {
  if (!devices.length) return undefined;

  if (selectedDeviceId && selectedDeviceId !== AUTO_MIC_ID) {
    const selected = devices.find((device) => device.deviceId === selectedDeviceId);
    if (selected) return selected;
  }

  return (
    devices.find((device) => isMacLikeMic(device.label) && !isIPhoneLikeMic(device.label)) ||
    devices.find((device) => !isIPhoneLikeMic(device.label)) ||
    devices[0]
  );
}

function buildAudioConstraints(deviceId?: string): MediaTrackConstraints {
  return {
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    channelCount: { ideal: 1 },
    sampleRate: { ideal: 48000 },
    sampleSize: { ideal: 16 },
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
}

export async function openPreferredAudioStream(selectedDeviceId = getStoredAudioDeviceId()) {
  const beforePermission = await listAudioInputDevices().catch(() => []);
  const preferredBefore = pickPreferredAudioInput(beforePermission, selectedDeviceId);

  if (preferredBefore?.deviceId) {
    return navigator.mediaDevices.getUserMedia({
      audio: buildAudioConstraints(preferredBefore.deviceId),
    });
  }

  const provisional = await navigator.mediaDevices.getUserMedia({
    audio: buildAudioConstraints(),
  });

  const afterPermission = await listAudioInputDevices().catch(() => []);
  const preferredAfter = pickPreferredAudioInput(afterPermission, selectedDeviceId);
  const activeTrack = provisional.getAudioTracks()[0];
  const activeDeviceId = activeTrack?.getSettings().deviceId || '';

  if (
    preferredAfter?.deviceId &&
    preferredAfter.deviceId !== activeDeviceId &&
    (selectedDeviceId !== AUTO_MIC_ID || isIPhoneLikeMic(activeTrack?.label || '') || !activeTrack?.label)
  ) {
    provisional.getTracks().forEach((track) => track.stop());
    return navigator.mediaDevices.getUserMedia({
      audio: buildAudioConstraints(preferredAfter.deviceId),
    });
  }

  return provisional;
}
