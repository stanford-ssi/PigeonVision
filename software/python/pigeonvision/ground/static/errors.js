// Keep independent failures visible when an unrelated subsystem recovers.
export class ErrorState {
  constructor() {
    this.components = new Map();
  }

  set(message, component = "viewer", recoverable = false) {
    this.components.set(component, {
      message: String(message),
      recoverable: recoverable === true,
    });
  }

  ready(component) {
    if (this.components.get(component)?.recoverable)
      this.components.delete(component);
  }

  get message() {
    return [...this.components.values()].map((error) => error.message).join("\n") || null;
  }
}
