let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function websocketProtocols(ticket: string): string[] {
  return ["campusvoice", `campusvoice.ticket.${ticket}`];
}
