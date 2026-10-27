import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

function at(path: string) {
    return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')));
});

describe('policy pages', () => {
    it.each([
        ['/privacy', /Privacy Policy/],
        ['/terms', /Terms of Use/],
        ['/cookies', /Cookie & Third-party Policy/],
    ])('%s renders and flags unset operator details', (url, heading) => {
        at(url);
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(heading);
        if (url !== '/cookies') expect(screen.getAllByText(/not configured/i).length).toBeGreaterThan(0);
    });

    it('footer links to every policy', () => {
        at('/privacy');
        const nav = screen.getByRole('navigation', { name: 'Legal' });
        for (const n of ['Privacy', 'Terms', 'Cookies & third parties']) {
            expect(nav).toHaveTextContent(n);
        }
    });
});

describe('third-party content is opt-in', () => {
    it('loads no YouTube iframe by default', () => {
        const { container } = at('/features/feature-a');
        expect(container.querySelector('iframe')).toBeNull();
    });

    it('loads the YouTube embed only after the visitor clicks, and remembers it', async () => {
        const { container } = at('/features/feature-a');
        const btn = screen.getAllByRole('button', { name: /Load video/ })[0];
        await userEvent.click(btn);
        expect(container.querySelector('iframe')?.getAttribute('src')).toContain('youtube-nocookie.com');
        expect(JSON.parse(window.localStorage.getItem('netrescue.consent.v1')!).youtube).toBe(true);
    });

    it('preferences dialog is modal, defaults to off, and Esc closes it', async () => {
        at('/cookies');
        await userEvent.click(screen.getByRole('button', { name: 'Third-party preferences' }));
        const dlg = screen.getByRole('dialog');
        expect(dlg).toHaveAttribute('aria-modal', 'true');
        for (const cb of screen.getAllByRole('checkbox')) expect(cb).not.toBeChecked();
        await userEvent.keyboard('{Escape}');
        expect(screen.queryByRole('dialog')).toBeNull();
    });
});
