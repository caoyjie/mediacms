import React, { useContext } from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { ThemeContext, ThemeProvider } from '../../../src/static/js/utils/contexts/ThemeContext';

jest.mock('../../../src/static/js/utils/settings/config', () => ({
  config: () => ({
    site: { id: 'theme-test' },
    theme: {
      mode: 'blossom',
      switch: { enabled: true, position: 'header' },
      logo: { lightMode: { img: '/light.png' }, darkMode: { img: '/dark.png' } },
    },
  }),
}));

let container;
const key = 'MediaCMS[theme-test][theme][mode]';
function Consumer() {
  const theme = useContext(ThemeContext);
  return <>
    <span>{theme.currentThemeMode}</span>
    <img src={theme.logo} alt="Site logo" />
    <button onClick={() => theme.changeThemeMode('dark')}>Dark</button>
    <button onClick={() => theme.changeThemeMode('aurora')}>Aurora</button>
  </>;
}
function render() {
  act(() => { ReactDOM.render(<ThemeProvider><Consumer /></ThemeProvider>, container); });
}
beforeEach(() => {
  localStorage.clear();
  document.body.className = 'existing-page-class';
  container = document.createElement('div');
  document.body.appendChild(container);
});
afterEach(() => {
  act(() => { ReactDOM.unmountComponentAtNode(container); });
  container.remove();
});

test('selecting a palette updates colors, logo, and persistent selection, including after remount', () => {
  render();
  expect(container.querySelector('span').textContent).toBe('blossom');
  act(() => container.querySelectorAll('button')[0].click());
  expect(container.querySelector('span').textContent).toBe('dark');
  expect(document.body.dataset.theme).toBe('dark');
  expect(document.body.classList.contains('dark_theme')).toBe(true);
  expect(document.body.classList.contains('existing-page-class')).toBe(true);
  expect(container.querySelector('img').getAttribute('src')).toBe('/dark.png');
  expect(JSON.parse(localStorage.getItem(key)).value).toBe('dark');
  act(() => { ReactDOM.unmountComponentAtNode(container); });
  render();
  expect(container.querySelector('span').textContent).toBe('dark');
  act(() => container.querySelectorAll('button')[1].click());
  expect(document.body.dataset.theme).toBe('aurora');
  expect(document.body.classList.contains('dark_theme')).toBe(false);
  expect(container.querySelector('img').getAttribute('src')).toBe('/light.png');
});

test.each(['dark', 'lagoon', 'meadow', 'light'])('restores a saved %s selection', (mode) => {
  localStorage.setItem(key, JSON.stringify({ value: mode, expire: Date.now() + 60000 }));
  render();
  expect(container.querySelector('span').textContent).toBe(mode);
});

test.each(['unknown', 'ocean', 'forest', 'sandstone', 'lavender'])('ignores retired or unknown cached theme %s', (value) => {
  localStorage.setItem(key, JSON.stringify({ value, expire: Date.now() + 60000 }));
  render();
  expect(container.querySelector('span').textContent).toBe('blossom');
});
