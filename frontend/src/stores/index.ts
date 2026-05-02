import { create } from 'zustand';

interface AppState {
  // Budget awareness
  monthlyUsedRmb: number;
  monthlyBudgetRmb: number;
  isBudgetWarning: boolean;
  isBudgetExceeded: boolean;
  setBudgetStatus: (status: {
    monthly_used_rmb: number;
    monthly_budget_rmb: number;
    is_budget_warning: boolean;
    is_budget_exceeded: boolean;
  }) => void;
}

export const useAppStore = create<AppState>((set) => ({
  monthlyUsedRmb: 0,
  monthlyBudgetRmb: 50,
  isBudgetWarning: false,
  isBudgetExceeded: false,
  setBudgetStatus: (status) =>
    set({
      monthlyUsedRmb: status.monthly_used_rmb,
      monthlyBudgetRmb: status.monthly_budget_rmb,
      isBudgetWarning: status.is_budget_warning,
      isBudgetExceeded: status.is_budget_exceeded,
    }),
}));
