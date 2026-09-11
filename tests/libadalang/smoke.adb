with Ada.Text_IO;
with Libadalang.Analysis;
with Langkit_Support.Text;
with VSS.Strings.Conversions;

procedure Smoke is
   use Libadalang.Analysis;
   use Langkit_Support.Text;
   Context : constant Analysis_Context := Create_Context;
   Unit    : constant Analysis_Unit := Context.Get_From_Buffer
     (Filename => "smoke_input.adb",
      Buffer   => "procedure Smoke_Input is begin null; end Smoke_Input;");
begin
   if Unit.Has_Diagnostics or else Unit.Root.Is_Null then
      raise Program_Error with "Libadalang could not parse valid Ada";
   end if;
   if Unit.Root.Text'Length = 0 then
      raise Program_Error with "Missing source text";
   end if;
   Ada.Text_IO.Put_Line
     (VSS.Strings.Conversions.To_UTF_8_String
        (VSS.Strings.Conversions.To_Virtual_String ("Static Libadalang OK")));
end Smoke;
