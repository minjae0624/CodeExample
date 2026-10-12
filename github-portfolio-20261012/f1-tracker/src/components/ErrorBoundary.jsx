import { Component } from 'react'
import { ErrorState } from './ui'

// Catches render-time errors in a page so one bad payload can't blank the app.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Surface in the console for debugging; no telemetry by design.
    console.error('PitWall page crash:', error, info?.componentStack)
  }

  // Reset the error on ANY navigation (including re-clicking the current nav
  // link) — without remounting healthy pages the way a key= would.
  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="page"><div className="container">
          <ErrorState
            label="Something broke on this page"
            error={this.state.error?.message || String(this.state.error)}
            onRetry={() => this.setState({ error: null })}
          />
        </div></div>
      )
    }
    return this.props.children
  }
}
