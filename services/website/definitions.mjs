// Only the dictionary definition changes language; the website remains English.
// A visitor's browser language never overrides the English-first default.
export const definitions = Object.freeze({
  en: 'A Denkzeug is an object that is not part of the body of a living or artificial organism, used to extend the functions of the mind in order to achieve a distant goal.',
  de: 'Ein Denkzeug ist ein nicht zum Körper eines lebenden oder künstlichen Organismus gehörendes Objekt, mit dessen Hilfe die Funktionen des Geistes erweitert werden, um auf diese Weise ein entferntes Ziel zu erreichen.',
  es: 'Un Denkzeug es un objeto que no forma parte del cuerpo de un organismo vivo o artificial y con cuya ayuda se amplían las funciones de la mente para alcanzar así un objetivo lejano.',
  fr: 'Un Denkzeug est un objet qui ne fait pas partie du corps d’un organisme vivant ou artificiel et qui permet d’étendre les fonctions de l’esprit afin d’atteindre un objectif lointain.',
  pt: 'Um Denkzeug é um objeto que não faz parte do corpo de um organismo vivo ou artificial e que permite ampliar as funções da mente para, assim, alcançar um objetivo distante.',
  it: 'Un Denkzeug è un oggetto che non fa parte del corpo di un organismo vivente o artificiale e con il cui aiuto si estendono le funzioni della mente per raggiungere così un obiettivo lontano.',
  zh: 'Denkzeug 是一种不属于生命体或人工有机体身体的物体，借助它可以扩展心智的功能，从而实现遥远的目标。',
  ja: 'Denkzeugとは、生きた有機体または人工的な有機体の身体の一部ではなく、精神の機能を拡張し、それによって遠くにある目標を達成するために用いられる物体である。',
  ko: 'Denkzeug는 살아 있는 유기체나 인공 유기체의 신체에 속하지 않는 물체로, 정신의 기능을 확장하여 먼 목표를 달성하는 데 사용된다.',
  hi: 'Denkzeug एक ऐसी वस्तु है जो किसी जीवित या कृत्रिम जीव के शरीर का हिस्सा नहीं है और जिसकी सहायता से मन की क्षमताओं का विस्तार किया जाता है, ताकि इस प्रकार किसी दूरस्थ लक्ष्य को प्राप्त किया जा सके।',
  ar: 'Denkzeug هو شيء لا يُعدّ جزءًا من جسم كائن حي أو اصطناعي، وتُوسَّع بواسطته وظائف العقل من أجل تحقيق هدف بعيد.',
  ru: 'Denkzeug — это объект, не являющийся частью тела живого или искусственного организма, с помощью которого расширяются функции разума, чтобы таким образом достичь отдалённой цели.'
})

export function definitionFor (locale) {
  const language = Object.hasOwn(definitions, locale) ? locale : 'en'
  return { language, text: definitions[language], direction: language === 'ar' ? 'rtl' : 'ltr' }
}

export function setupDefinition (document, storage) {
  const selector = document.getElementById('definition-language')
  const definition = document.getElementById('definition')
  if (!selector || !definition) return
  function apply (locale) {
    const value = definitionFor(locale)
    definition.textContent = value.text
    definition.lang = value.language
    definition.dir = value.direction
    selector.value = value.language
  }
  let saved
  try { saved = storage?.getItem('denkzeug-definition-language') } catch { /* Storage can be blocked. */ }
  apply(saved)
  selector.addEventListener('change', () => {
    apply(selector.value)
    try { storage?.setItem('denkzeug-definition-language', selector.value) } catch { /* The selection still works. */ }
  })
}
