/**
 * Tracks the newest request in a UI flow.
 * Older asynchronous responses may finish, but callers can ignore them when
 * their token is no longer current.
 */
export function createRequestSequence() {
  let current = 0;

  return {
    begin() {
      current += 1;
      return current;
    },
    current() {
      return current;
    },
    isCurrent(token) {
      return token === current;
    },
  };
}
