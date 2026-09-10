import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// jsdom has no WebGL, so MapLibre cannot construct a real map in tests.
vi.mock('maplibre-gl', () => {
  class FakeMap {
    on() {}
    off() {}
    addControl() {}
    addSource() {}
    addLayer() {}
    getSource() {
      return undefined;
    }
    setFeatureState() {}
    setLayoutProperty() {}
    fitBounds() {}
    easeTo() {}
    queryRenderedFeatures() {
      return [];
    }
    getCanvas() {
      return { style: {} };
    }
    remove() {}
  }
  class FakePopup {
    setLngLat() {
      return this;
    }
    setHTML() {
      return this;
    }
    addTo() {
      return this;
    }
    remove() {}
  }
  return {
    default: {
      Map: FakeMap,
      Popup: FakePopup,
      NavigationControl: class {},
      ScaleControl: class {},
    },
    Map: FakeMap,
    Popup: FakePopup,
    NavigationControl: class {},
    ScaleControl: class {},
  };
});

// Radix components rely on these; jsdom does not implement them.
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
