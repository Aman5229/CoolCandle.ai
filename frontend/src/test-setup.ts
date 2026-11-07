import '@testing-library/jest-dom/vitest'

// jsdom has no layout engine, so scrolling is a stub that only logs "Not implemented".
window.scrollTo = () => {}
