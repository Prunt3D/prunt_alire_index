with Ada.Numerics;
with Ada.Numerics.Long_Elementary_Functions;

procedure Runtime_Checks is
   package Math renames Ada.Numerics.Long_Elementary_Functions;
   Input : Long_Float := -1.0 with Volatile;
   Result : Long_Float with Volatile;
   Completed : Boolean := False;
begin
   begin
      Result := Math."**" (Input, 0.5);
      raise Program_Error;
   exception
      when Ada.Numerics.Argument_Error => null;
   end;

   --  Exercise libgnarl as well as libgnat, including its C support code.
   declare
      task Worker;
      task body Worker is
      begin
         Completed := True;
      end Worker;
   begin
      null;
   end;
   if not Completed then
      raise Program_Error;
   end if;
end Runtime_Checks;
