// CYPARK QR Generator — wraps qrcode-generator library
// Returns a base64 PNG data URL for the given text

function generateQR(text) {
  try {
    const qr = qrcode(0, 'M');
    qr.addData(text);
    qr.make();
    // Get as image tag, extract src
    const imgTag = qr.createImgTag(6, 8);
    const match = imgTag.match(/src="([^"]+)"/);
    if (match) return match[1];
  } catch(e) {}
  // Fallback: return empty placeholder
  return '';
}

function makeQRData(session_id, slot_id, plate, entry_time) {
  return `CYPARK|${session_id}|${slot_id}|${plate}|${entry_time}`;
}

function parseQR(qrString) {
  try {
    const parts = qrString.trim().split('|');
    if (parts.length === 5 && parts[0] === 'CYPARK') {
      return { session_id: parts[1], slot_id: parts[2], plate: parts[3], entry: parts[4] };
    }
  } catch (e) {}
  return null;
}
