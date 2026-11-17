import { API_URL } from './config';

/**
 * Handle API error responses gracefully.
 */
async function handleResponse(response) {
  if (!response.ok) {
    let errorDetail = `Request failed with status ${response.status}`;
    try {
      const data = await response.json();
      if (data && data.detail) {
        errorDetail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      } else if (data && data.message) {
        errorDetail = data.message;
      }
    } catch {
      // response not JSON
    }
    const err = new Error(errorDetail);
    err.status = response.status;
    throw err;
  }
  return response.json();
}

/**
 * Check backend health and DB status.
 */
export async function checkHealth() {
  const res = await fetch(`${API_URL}/api/health`, {
    headers: { 'Accept': 'application/json' },
  });
  return handleResponse(res);
}

/**
 * Customer Demo Login.
 * Real auth is out of scope.
 */
export async function loginCustomer(email) {
  const res = await fetch(`${API_URL}/api/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  });
  return handleResponse(res);
}

/**
 * Send a chat message.
 */
export async function sendChatMessage(sessionId, message) {
  const res = await fetch(`${API_URL}/api/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      message: message.trim(),
    }),
  });
  return handleResponse(res);
}

/**
 * Fetch messages for a session (incremental polling supported).
 */
export async function getSessionMessages(sessionId, after = null) {
  const url = new URL(`${API_URL}/api/sessions/${sessionId}/messages`);
  if (after !== null && after !== undefined) {
    url.searchParams.set('after', String(after));
  }
  const res = await fetch(url.toString(), {
    headers: { 'Accept': 'application/json' },
  });
  return handleResponse(res);
}

/**
 * Admin: List refunds (default: pending).
 */
export async function getAdminRefunds(adminToken, status = 'pending') {
  const url = new URL(`${API_URL}/api/admin/refunds`);
  if (status) {
    url.searchParams.set('status', status);
  }
  const res = await fetch(url.toString(), {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': adminToken,
    },
  });
  return handleResponse(res);
}

/**
 * Admin: Approve or Reject a refund.
 */
export async function decideAdminRefund(adminToken, refundId, decision, note = '') {
  const res = await fetch(`${API_URL}/api/admin/refunds/${refundId}/decision`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-Admin-Token': adminToken,
    },
    body: JSON.stringify({
      decision,
      note: note.trim(),
    }),
  });
  return handleResponse(res);
}

/**
 * Admin: List handoffs (default: open).
 */
export async function getAdminHandoffs(adminToken, status = 'open') {
  const url = new URL(`${API_URL}/api/admin/handoffs`);
  if (status) {
    url.searchParams.set('status', status);
  }
  const res = await fetch(url.toString(), {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': adminToken,
    },
  });
  return handleResponse(res);
}

/**
 * Admin: Get single handoff detail and full conversation transcript.
 */
export async function getAdminHandoffDetail(adminToken, handoffId) {
  const res = await fetch(`${API_URL}/api/admin/handoffs/${handoffId}`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': adminToken,
    },
  });
  return handleResponse(res);
}

/**
 * Admin: Resolve a handoff.
 */
export async function resolveAdminHandoff(adminToken, handoffId) {
  const res = await fetch(`${API_URL}/api/admin/handoffs/${handoffId}/resolve`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': adminToken,
    },
  });
  return handleResponse(res);
}
