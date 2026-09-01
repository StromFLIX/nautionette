import { createApp } from 'vue'
import { Quasar, Dark, Notify, Dialog, Loading } from 'quasar'
import '@quasar/extras/material-icons/material-icons.css'
import 'quasar/dist/quasar.css'
import './style.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(Quasar, {
  plugins: { Notify, Dialog, Loading },
  config: { dark: true, brand: { primary: '#6ee7b7', secondary: '#3b82f6', dark: '#0b1020' } }
})
app.use(router)
Dark.set(true)
app.mount('#app')
