const ACCESS = "access_token";
const REFRESH = "refresh_token";

export function getAccessToken() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(ACCESS);
}

export function getRefreshToken() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(REFRESH);
}

export function setTokens(access: string, refresh: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(ACCESS, access);
  window.localStorage.setItem(REFRESH, refresh);
}

export function clearTokens() {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(ACCESS);
  window.localStorage.removeItem(REFRESH);
}
