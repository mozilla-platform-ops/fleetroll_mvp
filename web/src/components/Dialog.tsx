import * as RadixDialog from "@radix-ui/react-dialog";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: React.ReactNode;
}

export function Dialog({ open, onOpenChange, title, children }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 bg-black/40" />
        <RadixDialog.Content className="fixed left-1/2 top-1/2 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg bg-white p-6 shadow-xl">
          <RadixDialog.Title className="text-heading mb-4">{title}</RadixDialog.Title>
          {children}
          <RadixDialog.Close className="absolute right-4 top-4 text-status-unknown hover:text-status-idle">
            ✕
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
