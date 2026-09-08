import { createApp } from 'vue'
import { Dialog, Notify, Quasar } from 'quasar'
import '@quasar/extras/material-icons/material-icons.css'
import 'quasar/dist/quasar.css'
import './styles/base.css'

import App from './App.vue'
import router, { seedHistory } from './router'
import { watchKeyboard } from './keyboard'
import { startPreferences } from './preferences'

const app = createApp(App)
app.use(Quasar, {
  plugins: { Notify, Dialog },
  config: {
    notify: { position: 'bottom', timeout: 4000 }
  }
})
app.use(router)
startPreferences()
watchKeyboard()
seedHistory().then(() => app.mount('#app'))
