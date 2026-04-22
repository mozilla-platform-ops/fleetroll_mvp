import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Uncaught error:", error, info);
  }

  override render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex h-screen flex-col items-center justify-center gap-2 p-8">
          <h1 className="text-heading text-status-crit">Something went wrong</h1>
          <pre className="font-mono text-caption text-status-unknown">
            {this.state.error?.message}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}
