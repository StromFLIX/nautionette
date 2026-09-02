import { createApp } from 'vue'
import { Dark, Dialog, Notify, Quasar } from 'quasar'
import '@quasar/extras/material-icons/material-icons.css'
import 'quasar/dist/quasar.css'
import './styles/base.css'

import App from './App.vue'
import router, { seedHistory } from './router'
import { watchKeyboard } from './keyboard'

const app = createApp(App)
app.use(Quasar, {
  plugins: { Notify, Dialog },
  config: {
    dark: true,
    brand: { primary: '#4f8cff', secondary: '#3fb950', negative: '#f0616d', dark: '#15181f' },
    notify: { position: 'bottom', timeout: 4000, textColor: 'white' }
  }
})
app.use(router)
Dark.set(true)
watchKeyboard()
seedHistory().then(() => app.mount('#app'))
