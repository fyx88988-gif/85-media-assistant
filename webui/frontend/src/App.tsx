import { Workbench } from './components/Workbench'

export function App() {
  const params = new URLSearchParams(window.location.search)
  const queryToken = params.get('session')
  if (queryToken) {
    sessionStorage.setItem('85-session', queryToken)
    params.delete('session')
    const query = params.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`)
  }
  return <Workbench sessionToken={queryToken || sessionStorage.getItem('85-session') || ''} />
}
