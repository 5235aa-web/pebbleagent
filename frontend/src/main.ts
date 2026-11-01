import { createApp } from 'vue'
import './styles/orbit.css'
import './styles/app.css'
import App from './App.vue'
import { initOrbit } from './orbit/runtime.js'
import { connectGateway } from './data/wire'

// Mount first: the runtime queries the DOM the prototype markup produced.
createApp(App).mount('#app')
initOrbit()
// The surface keeps the seeded content until a session answers, so a missing
// gateway changes nothing on screen.
connectGateway()
